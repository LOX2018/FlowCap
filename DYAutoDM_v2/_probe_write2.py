import os, shutil, sys
test = r'C:\temp\dyautodm_test'
src = r'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\dist\DYAutoDM_v2_0.10.0.exe'
lines = []
try:
    with open(os.path.join(test, '_writetest.txt'), 'w') as f:
        f.write('hi')
    lines.append('small: OK' if os.path.exists(os.path.join(test, '_writetest.txt')) else 'small: FAILED')
except Exception as e:
    lines.append('small ERROR: %s' % e)
for nm in ['DYAutoDM_v2_0.10.0.exe', 'test_010.exe']:
    dst = os.path.join(test, nm)
    try:
        shutil.copy2(src, dst)
        lines.append('%s -> %s' % (nm, 'OK' if os.path.exists(dst) else 'FAILED'))
    except Exception as e:
        lines.append('%s -> ERROR: %s' % (nm, e))
with open(r'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\_probe_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('\n'.join(lines))
