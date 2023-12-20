import asyncio
import pymodbus.client as ModbusClient
import pymodbus.framer
from datetime import datetime, timedelta
import logging
from enum import Enum
import time

_logger = logging.getLogger(__file__)
_logger.setLevel("DEBUG")

class SampleUnits(Enum):
    TC = 0x00   # total counts during the sample time
    CF = 0x01   # number of particles per cubic foot
    L  = 0x02   # number of particles per liter

class DetectedData(dict):
    allowed_keys = ['PC0.3', 'PC0.5', 'PC0.7', 'PC1.0', 'PC2.5', 'PC5.0', 'PC10']

    def __init__(self, mapping=None, **kwargs):
        if mapping is not None:
            for k in mapping.keys():
                if k not in self.allowed_keys:
                    raise KeyError
            for k in self.allowed_keys:
                if k not in mapping:
                    mapping[k] = 0
        else:
            mapping = {k: 0 for k in self.allowed_keys}
        if kwargs:
            for k in kwargs.keys():
                if k not in self.allowed_keys:
                    raise KeyError
            mapping.update(kwargs)
        super().__init__(mapping)

    def __setitem__(self, key, value):
        if key not in self.allowed_keys:
            raise KeyError
        super().__setitem__(key, value)

    def __add__(self, other):
        temp = DetectedData()
        for k in self.allowed_keys:
            temp[k] = self[k] + other[k]

        return temp

    def __eq__(self, other):
        if not isinstance(other, DetectedData):
            return NotImplemented
        for k in self.allowed_keys:
            if self[k] != other[k]:
                return False
        return True

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):
        return '{{{}}}'.format(', '.join([f"'{k}': {self[k]}" for k in self.allowed_keys]))


class PMD331:
    flow_rate_Lpm = 2.83 # flow rate in liters per minute
    flow_rate_cfm = 2.83 * 0.03531467 # flow rate in cubic feet per minute

    '''Class provides raw access to PMD331 functions'''
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.client = None

    async def startup(self):
        '''Initialize serial connection'''

        # self.client = ModbusClient.AsyncModbusTcpClient(
        #     self.host,
        #     self.port,
        #     framer=pymodbus.framer.ModbusRtuFramer,
        #     # framer=pymodbus.framer.ModbusSocketFramer,
        #     baudrate=115200,
        #     bytesize=8,
        #     parity='N',
        #     stopbits=1
        # )

        self.client = ModbusClient.AsyncModbusSerialClient(
            self.port,
            framer=pymodbus.framer.ModbusRtuFramer,
            baudrate=115200,
            bytesize=8,
            parity='N',
            stopbits=1
        )

        # this connect call connects the application to the host's device port, not
        # the pmd331
        await self.client.connect()
        assert self.client.connected

    @property
    async def started(self):
        '''Has startup() been called'''
        return self.client is not None and self.client.connected

    async def start_detection(self):
        '''Start detection'''
        await self.client.write_register(address=0x06, value=0x01, slave=0xFE)

    async def stop_detection(self):
        '''Stop detection'''
        await self.client.write_register(address=0x06, value=0x00, slave=0xFE)

    async def read_data(self) -> DetectedData:
        '''Reports the current live reading, as displayed on the device.'''
        rr = await self.client.read_input_registers(address=0x03, count=0x0E, slave=0xFE)
        dd = DetectedData()
        dd['PC0.3'] = ((rr.registers[0]  << 8) | rr.registers[1])
        dd['PC0.5'] = ((rr.registers[2]  << 8) | rr.registers[3])
        dd['PC0.7'] = ((rr.registers[4]  << 8) | rr.registers[5])
        dd['PC1.0'] = ((rr.registers[6]  << 8) | rr.registers[7])
        dd['PC2.5'] = ((rr.registers[8]  << 8) | rr.registers[9])
        dd['PC5.0'] = ((rr.registers[10] << 8) | rr.registers[11])
        dd['PC10']  = ((rr.registers[12] << 8) | rr.registers[13])

        return dd

    async def set_clock(self, t: datetime):
        await self.client.write_registers(address=0x64,
            values=[t.year & 0xffff, t.month & 0xffff, t.day & 0xffff,
                    t.hour & 0xffff, t.minute & 0xffff, t.second & 0xffff],
            slave=0xFE)

    @property
    async def sample_units(self) -> SampleUnits:
        '''Units for reporting particle counts.
        CF: # per cubic foot
        L:  # per liter
        TC: total count during the sample time'''
        rr = await self.client.read_holding_registers(address=0x04, slave=0xFE)
        return SampleUnits(rr.registers[0])

    async def set_sample_units(self, units: SampleUnits):
        await self.client.write_register(address=0x04, value=units.value, slave=0xFE)

    @property
    async def sample_time(self) -> int:
        '''The interval in seconds over which samples are averaged (CF/L) or totaled (TC).
        Valid range is 3-60s.'''
        rr = await self.client.read_holding_registers(address=0x05, slave=0xFE)
        return rr.registers[0]

    async def set_sample_time(self, t: int):
        if t < 3 or t > 60:
            raise ValueError(f'Sample time {t} invalid, must be in [3,60]')
        await self.client.write_register(address=0x05, value=t, slave=0xFE)

class Sample:
    '''Represents a sample'''
    def __init__(self, sample_group: int, dd: DetectedData, timestamp=datetime.now()):
        self._sample_group = sample_group
        self._dd = dd
        self._timestamp = timestamp

    @property
    def sample_group(self) -> int:
        return self._sample_group

    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    @property
    def data(self) -> DetectedData:
        return self._dd

class Sampler:
    '''Emulate sampling interval behavior of PMD331'''
    def __init__(
            self,
            pmd331,
            sample_units: SampleUnits = SampleUnits.L,
            sample_time: int = 60,
            sync_clock: bool = False):
        self.pmd331 = pmd331
        self.sample_units = sample_units
        self.sample_time = sample_time
        self._samples = asyncio.Queue()
        self._started = False
        self._closed = False
        self._sample_task = None
        self._sync_clock = sync_clock

    async def __aenter__(self):
        await self.start(self._sync_clock)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.end()

    async def start(self, sync_clock: bool = False):
        '''Note: This will break if the sample time or sample units are manually changed
              on the device while the sample is being collected.
        '''
        if self._started:
            return
        self._started = True

        if not await self.pmd331.started:
            await self.pmd331.startup()
        # synchronize the clock
        if sync_clock:
            await self.pmd331.set_clock(datetime.now())
        # set the sample units
        await self.pmd331.set_sample_units(SampleUnits.TC) # hard set this to TC to allow overflow handling
        # set the sample time
        await self.pmd331.set_sample_time(self.sample_time)
        # start detection
        await self.pmd331.start_detection()

        async def sample_task(pmd331: PMD331, units: SampleUnits, t: int):
            begin_date = datetime.now()
            begin_t = time.perf_counter()
            sample_group = 0

            while not self._closed:
                fast_samples = []
                t0_sample = time.perf_counter()
                sample_group = sample_group + 1
                overflowcounter = DetectedData()
                last_dd = None
                for _ in range(t):
                    dd = await pmd331.read_data()
                    if self._closed:
                        return
                    elapsed_total = time.perf_counter() - begin_t
                    # for each bin, if this sample is smaller than the last sample increase the overflow
                    # counter for that bin
                    # then adjust the sample by the overflow counter
                    # then adjust sample units to flow if desired
                    if last_dd is not None:
                        for k in DetectedData.allowed_keys:
                            if dd[k] < last_dd[k]:
                                overflowcounter[k] = overflowcounter[k] + 1
                        last_dd = dd.copy()

                        for k in DetectedData.allowed_keys:
                            # adjust current record for overflow
                            dd[k] = dd[k] + 2**16 * overflowcounter[k]
                            if units == SampleUnits.TC:
                                continue
                            # adjust current record for flow rate
                            rate = PMD331.flow_rate_cfm if units == SampleUnits.CF else PMD331.flow_rate_Lpm
                            # convert rate to per second
                            rate = rate / 60
                            volume = rate * (_ + 1)
                            dd[k] = int(dd[k] / volume)
                    else:
                        last_dd = dd.copy()

                    _logger.debug(f'    {t-_} {dd}')

                    if _ > 0:  # throw away the first sample it's always all zeroes
                        fast_samples.append(Sample(sample_group, dd, begin_date + timedelta(seconds=elapsed_total)))

                    # adjust the sleep to account for time lost by reading the data
                    # and the program doing other things
                    elapsed_sample = time.perf_counter() - t0_sample - _
                    await asyncio.sleep(1 - elapsed_sample)
                    if self._closed:
                        return

                await self._samples.put(fast_samples)

        self._sample_task = asyncio.create_task(sample_task(self.pmd331, self.sample_units, self.sample_time))

    async def end(self):
        if not self._started or self._closed:
            return

        self._closed = True # signals sample_task to stop
        # wait for sample_task to finish, otherwise the writes to the serial port
        # could step on each other
        await asyncio.wait((self._sample_task, ))
        await self.pmd331.stop_detection()

    async def read(self) -> list[Sample]:
        '''Returns results of one sample interval, blocks if none are available

        Note: Only returns N-1 samples (where N is the sample time). The final sample,
        which is written to the device's log data store, is not available over the RS232
        interface. Since each entry in the array is cumulative, use the last sample in this
        set with the caveat that it's actually for an interval of N-1.

        example:
          s = await sampler.read()
          s[-1].data  # access the data for the N-1 sample
        '''
        return await self._samples.get()
