import os, glob

dirs = [
    r'C:\temp\dyautodm_test',
    r'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\dist',
    r'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\src-tauri\target\release',
    r'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\src-tauri\target\release\bundle',
]
out = []
for d in dirs:
    out.append('=== %s ===' % d)
    if not os.path.exists(d):
        out.append('  (NOT EXISTS)')
        continue
    for root, _, files in os.walk(d):
        for f in files:
            if f.lower().endswith('.exe') or f.lower().endswith('.msi') or 'dyautodm' in f.lower() or 'DYAutoDM' in f:
                p = os.path.join(root, f)
                try:
                    sz = os.path.getsize(p)
                    mt = os.path.getmtime(p)
                    import datetime
                    mt = datetime.datetime.fromtimestamp(mt).strftime('%Y/%m/%d %H:%M:%S')
                except Exception:
                    sz, mt = '-', '-'
                out.append('  %-60s %12s  %s' % (f, sz, mt))
out.append('')
with open(os.path.join(os.path.dirname(__file__), '_deploy_check.txt'), 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(out))
print('\n'.join(out))
