# pypmd331
Python library for accessing temtop pmd331 over rs232

There is a dependency on `pymodbus`. Install it with
```
pip install pymodbus
```
or
```
pip install -r requirements.txt
```

**Important** ensure the serial baud rate setting on the pmd331 matches the setting in `pmd331.py` of 115200. 

example:
```
import asyncio
import argparse
from pmd331 import PMD331, Sampler, SampleUnits

async def main(samples, sample_time, sample_units):
    pmd = PMD331('/dev/ttyUSB0')
	
    async with Sampler(pmd, SampleUnits[sample_units], sample_time,
                       sync_clock=False   # don't sync the pmd331 clock to the host system clock
        ) as sampler:
        print(f'Sampling {samples} times with a {sample_time}s interval in {sample_units}...')
        for _ in range(samples):
            sample = (await sampler.read())[-1]
            dd = sample.data
            ts = sample.timestamp.isoformat(timespec='seconds')
            print('{} PC0.3: {:6d} PC0.5: {:6d} PC0.7: {:6d} PC1.0: {:6d}'.format(
                    ts, dd['PC0.3'], dd['PC0.5'], dd['PC0.7'], dd['PC1.0']))
            print(' ' * len(ts) + ' PC2.5: {:6d} PC5.0: {:6d}  PC10: {:6d}'.format(
                    dd['PC2.5'], dd['PC5.0'], dd['PC10']))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--sample-time', type=int, default=60, choices=range(3,61))
    parser.add_argument('--sample-units', default='L', choices=['L', 'CF', 'TC'])

    args = parser.parse_args()

    asyncio.run(main(args.samples, args.sample_time, args.sample_units))
```

See `monitor_pmd331.py` for a more complicated example usage that writes two samples to a csv file. It has a dependency on the `aiofiles` package, which can be installed with pip.
