"""Debug: dump raw directory entry bytes before and after write."""
import struct
import subprocess
import re
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

# Use existing btime.py helpers for boot parsing
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from btime import (
    _exfat_parse_boot, _exfat_find_in_dir, _exfat_read_device, _exfat_encode_time,
    _fix_exfat_raw
)

tmp = Path(tempfile.mkdtemp(prefix='gopro_debug_'))
img = tmp / 'sdcard.img'
subprocess.run(['cp', '--sparse=always', 'test/sdcard.img', str(img)], check=True, capture_output=True)
r = subprocess.run(['udisksctl', 'loop-setup', '-f', str(img), '--no-user-interaction'],
                   capture_output=True, text=True)
loop = re.search(r'as (/dev/loop\d+)', r.stdout).group(1)
print(f'Loop: {loop}')

# Mount
subprocess.run(['udisksctl', 'mount', '-b', loop, '--no-user-interaction'], capture_output=True, text=True)

boot = _exfat_parse_boot(loop)
print(f'Boot: cs={boot["cluster_size"]}, heap_off={boot["cluster_heap_offset"]}, root={boot["root_cluster"]}')

# Find GL010063.LRV in the file system
# First find DCIM
found = _exfat_find_in_dir(boot, loop, boot['root_cluster'], 'DCIM')
if found:
    dchain, dci, doff, dsc, dentries = found
    stream = dentries[1]
    dcim_cl = struct.unpack_from('<I', stream, 0x14)[0]
    print(f'DCIM first_cluster: {dcim_cl}')
    
    # Find 100GOPRO in DCIM
    found2 = _exfat_find_in_dir(boot, loop, dcim_cl, '100GOPRO')
    if found2:
        dchain2, dci2, doff2, dsc2, dentries2 = found2
        stream2 = dentries2[1]
        gopro_cl = struct.unpack_from('<I', stream2, 0x14)[0]
        print(f'100GOPRO first_cluster: {gopro_cl}')
        
        # Read the cluster and dump first bytes
        cl_off = boot['cluster_heap_offset'] + (gopro_cl - 2) * boot['cluster_size']
        cl_data = _exfat_read_device(loop, cl_off, boot['cluster_size'])
        print(f'\nFirst 256 bytes of 100GOPRO cluster (hex):')
        for row in range(16):
            row_off = row * 16
            hex_bytes = ' '.join(f'{b:02x}' for b in cl_data[row_off:row_off+16])
            print(f'  {row_off:04x}: {hex_bytes}')
        
        # Find the first file entry
        found3 = _exfat_find_in_dir(boot, loop, gopro_cl, 'GL010063.LRV')
        if found3:
            fchain, fci, foff, fsc, fentries = found3
            print(f'\nFile entry found! chain_idx={fci}, cluster_offset={foff}')
            print(f'Entry set ({1+fsc} entries, {32*(1+fsc)} bytes):')
            
            entry = fentries[0]
            print(f'\nFile Directory Entry (32 bytes):')
            for row in range(2):
                row_off = row * 16
                hex_bytes = ' '.join(f'{b:02x}' for b in entry[row_off:row_off+16])
                print(f'  {row_off:04x}: {hex_bytes}')
            
            # Show relevant fields
            cd = struct.unpack_from('<H', entry, 0x06)[0]
            ct = struct.unpack_from('<H', entry, 0x08)[0]
            ct_ms = entry[0x0A]
            print(f'\nBefore write:')
            print(f'  create_date (offset 0x06): {cd} = 0x{cd:04x}')
            print(f'  create_time (offset 0x08): {ct} = 0x{ct:04x}')
            print(f'  create_time_ms (offset 0x0A): {ct_ms}')
            year = 1980 + (cd >> 9)
            month = (cd >> 5) & 0x0F
            day = cd & 0x1F
            hour = ct >> 11
            minute = (ct >> 5) & 0x3F
            sec = (ct & 0x1F) * 2 + (ct_ms // 100)
            print(f'  Decoded: {year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{sec:02d}')
            
            # Now write a known btime via our function
            target_dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
            print(f'\nWriting btime to {target_dt}...')
            _fix_exfat_raw(str(Path('/run/media/michi/7B37-D46E42') / 'DCIM' / '100GOPRO' / 'GL010063.LRV'),
                          target_dt, dry_run=False)
            
            # Read the cluster again after write
            cl_data2 = _exfat_read_device(loop, cl_off, boot['cluster_size'])
            post_entry = cl_data2[foff:foff+32]
            
            print(f'\nFile Directory Entry after write (32 bytes):')
            for row in range(2):
                row_off = row * 16
                hex_bytes = ' '.join(f'{b:02x}' for b in post_entry[row_off:row_off+16])
                print(f'  {row_off:04x}: {hex_bytes}')
            
            cd2 = struct.unpack_from('<H', post_entry, 0x06)[0]
            ct2 = struct.unpack_from('<H', post_entry, 0x08)[0]
            ct_ms2 = post_entry[0x0A]
            print(f'\nAfter write:')
            print(f'  create_date (offset 0x06): {cd2} = 0x{cd2:04x}')
            print(f'  create_time (offset 0x08): {ct2} = 0x{ct2:04x}')
            print(f'  create_time_ms (offset 0x0A): {ct_ms2}')
            year2 = 1980 + (cd2 >> 9)
            month2 = (cd2 >> 5) & 0x0F
            day2 = cd2 & 0x1F
            hour2 = ct2 >> 11
            minute2 = (ct2 >> 5) & 0x3F
            sec2 = (ct2 & 0x1F) * 2 + (ct_ms2 // 100)
            print(f'  Decoded: {year2}-{month2:02d}-{day2:02d} {hour2:02d}:{minute2:02d}:{sec2:02d}')
            
            # What we wrote
            dw, tw, tm = _exfat_encode_time(target_dt)
            print(f'\nWhat we expected to write:')
            print(f'  date_word: {dw} = 0x{dw:04x}')
            print(f'  time_word: {tw} = 0x{tw:04x}')
            print(f'  time_ms: {tm}')
        else:
            print('Could not find GL010063.LRV')
    else:
        print('Could not find 100GOPRO')
else:
    print('Could not find DCIM')

# Cleanup
subprocess.run(['udisksctl', 'unmount', '-b', loop, '--no-user-interaction'], capture_output=True)
subprocess.run(['sudo', 'losetup', '-d', loop], capture_output=True)
shutil.rmtree(tmp, ignore_errors=True)
