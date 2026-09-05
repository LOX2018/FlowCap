$url = "https://github.com/nashsu/llm_wiki/releases/download/v0.6.11/LLM-Wiki-0.6.11-windows-x64-portable.zip"
$out = "C:\Users\LOX\Desktop\DYchajian\LLM-Wiki\LLM-Wiki-0.6.11-windows-x64-portable.zip"
Write-Host "downloading LLM Wiki windows portable zip..."
Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
$f = Get-Item $out
Write-Host ("saved: " + $f.FullName + " size=" + $f.Length)
