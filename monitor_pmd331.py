import asyncio
from pmd331 import PMD331, SampleUnits, Sampler
import time
import aiofiles
from aiocsv import AsyncWriter
import logging

async def main():
    logging.basicConfig(filename='monitor_pmd331.log', level=logging.DEBUG)

    pmd = PMD331('/dev/ttyUSB0')
    await pmd.startup()

    units = await pmd.sample_units
    interval = await pmd.sample_time
    print(f'Units: {units}, Interval: {interval}')

    async with aiofiles.open('pmd331.csv', 'w') as f:
        csvwriter = AsyncWriter(f)
        await csvwriter.writerow(['Sample', 'timestamp', 'PC0.3', 'PC0.5', 'PC0.7', 'PC1.0', 'PC2.5', 'PC5.0', 'PC10'])
        async with Sampler(pmd, SampleUnits.TC, 20, sync_clock=False) as sampler:
            start = time.perf_counter()
            for _ in range(2):
                sample = await sampler.read()
                elapsed = time.perf_counter() - start
                print('{:05.2f}: {}'.format(elapsed, sample[-1].data))
                for record in sample:
                    dd = record.data
                    await csvwriter.writerow([
                        record.sample_group, record.timestamp,
                        dd['PC0.3'], dd['PC0.5'], dd['PC0.7'], dd['PC1.0'], dd['PC2.5'],
                        dd['PC5.0'], dd['PC10']
                    ])

if __name__ == '__main__':
    asyncio.run(main())