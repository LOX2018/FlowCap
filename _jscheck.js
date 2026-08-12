const fs = require('fs');
const html = fs.readFileSync('web/index.html', 'utf8');
// 提取所有 <script>...</script>（非 src 引用）的内容
const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
let m, idx = 0, allOk = true;
while ((m = re.exec(html)) !== null) {
  const code = m[1];
  if (!code.trim()) continue;
  idx++;
  try {
    new Function(code);
    console.log('script #' + idx + ' OK (' + code.length + ' chars)');
  } catch (e) {
    allOk = false;
    console.log('script #' + idx + ' SYNTAX ERROR: ' + e.message);
  }
}
console.log(allOk ? 'JS_ALL_OK' : 'JS_HAS_ERROR');
