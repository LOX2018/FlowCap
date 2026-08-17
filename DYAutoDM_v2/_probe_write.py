import os
test = r'C:\temp\dyautodm_test'
# 测试1：写一个小文件
try:
    with open(os.path.join(test, '_writetest.txt'), 'w') as f:
        f.write('hi')
    print('small file write: OK' if os.path.exists(os.path.join(test, '_writetest.txt')) else 'small file write: FAILED')
except Exception as e:
    print('small file write ERROR:', e)

# 测试2：复制一个 exe 用不同名字
src = r'C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\dist\DYAutoDM_v2_0.10.0.exe'
for nm in ['DYAutoDM_v2_0.10.0.exe', 'DYAutoDM_v2_0.10.0_test.exe', 'test_010.exe']:
    dst = os.path.join(test, nm)
    try:
        import shutil
        shutil.copy2(src, dst)
        print(nm, 'OK' if os.path.exists(dst) else 'FAILED(no file)')
    except Exception as e:
        print(nm, 'ERROR:', e)
