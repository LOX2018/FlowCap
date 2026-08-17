import os, shutil

root = r'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2'
dist = os.path.join(root, 'dist')
test = r'C:\temp\dyautodm_test'
test_bin = os.path.join(test, 'binaries')

src_main = os.path.join(dist, 'DYAutoDM_v2_0.10.0.exe')
print('dist main exists:', os.path.exists(src_main), os.path.getsize(src_main) if os.path.exists(src_main) else '-')

# 停占用
import subprocess
for nm in ['dyautodm-v2', 'DYAutoDM_v2_0.10.0', 'dyautodm-backend-x86_64-pc-windows-msvc',
           'dyautodm-browser-daemon-x86_64-pc-windows-msvc', 'dyautodm-recv-daemon-x86_64-pc-windows-msvc']:
    try:
        subprocess.run(['taskkill', '/F', '/IM', nm + '.exe'], capture_output=True, timeout=10)
    except Exception as e:
        print('kill', nm, e)

os.makedirs(test_bin, exist_ok=True)

# 主程序：带版本号 + 固定名
dst_ver = os.path.join(test, 'DYAutoDM_v2_0.10.0.exe')
shutil.copy2(src_main, dst_ver)
shutil.copy2(src_main, os.path.join(test, 'dyautodm-v2.exe'))

# sidecar
for s in ['dyautodm-backend', 'dyautodm-browser-daemon', 'dyautodm-recv-daemon']:
    name = s + '-x86_64-pc-windows-msvc.exe'
    src = os.path.join(dist, name)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(test_bin, name))
        print('copied sidecar', name, os.path.getsize(src))

print('VER_MAIN:', os.path.exists(dst_ver), os.path.getsize(dst_ver) if os.path.exists(dst_ver) else '-')
print('DONE')
