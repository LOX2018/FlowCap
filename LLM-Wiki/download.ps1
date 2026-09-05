$url = "https://github.com/nashsu/llm_wiki/releases/download/v0.6.11/LLM.Wiki_0.6.11_x64-setup.exe"
$out = "C:\Users\LOX\Desktop\DYchajian\LLM-Wiki\LLM.Wiki_0.6.11_x64-setup.exe"
Write-Host "downloading LLM Wiki windows installer..."
Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
$f = Get-Item $out
Write-Host ("saved: " + $f.FullName + " size=" + $f.Length)
