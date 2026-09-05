import os
p = r"C:\Users\LOX\Desktop\DYchajian\LLM-Wiki\LLM.Wiki_0.6.11_x64-setup.exe"
with open(p, "rb") as f:
    magic = f.read(4)
print("magic:", magic)
print("size:", os.path.getsize(p))
