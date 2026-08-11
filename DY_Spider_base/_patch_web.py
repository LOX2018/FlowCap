import io

p = 'web/index.html'
s = io.open(p, encoding='utf-8').read()

# 1) CDN -> 本地 vendor
s = s.replace('https://unpkg.com/react@18.3.1/umd/react.development.js', 'vendor/react.development.js')
s = s.replace('https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js', 'vendor/react-dom.development.js')
s = s.replace('https://unpkg.com/@babel/standalone@7.29.0/babel.min.js', 'vendor/babel.min.js')
s = s.replace('https://unpkg.com/framer-motion@11.11.13/dist/framer-motion.js', 'vendor/framer-motion.js')

# 2) 修复设计稿 bug: URL.createObjecturl(无效对象) -> 合法 Blob 下载(占位)
bad = "/api/projects/4d03cd9d-4edf-4ace-9592-eb7aa97e298e/raw/blob?workspaceId=xl31pfpy01561g39my0yf7rx&workspaceMemberId=fwqc99owe2nyh67ddu1fycy9"
good = "URL.createObjectURL(new Blob([JSON.stringify(exportData,null,2)],{type:'application/json'}))"
s = s.replace('URL.createObjecturl(' + bad + ')', good)
s = s.replace('URL.createObjecturl(' + bad + ')', good)

# 3) 注入 pywebview 桥接(默认 mock, pywebviewready 后由后端覆盖)
bridge = r'''
<script>
  // 后端桥接：测试版默认用原型 mock 数据，pywebview 加载后由 Python 注入真实 API
  window.ApiBridge = {
    ready: false,
    getOverview: function(){ return Promise.resolve(null); },
    getStats: function(){ return Promise.resolve(null); },
    getConversations: function(){ return Promise.resolve(null); },
    getAccounts: function(){ return Promise.resolve(null); },
    getTasks: function(){ return Promise.resolve(null); },
    sendDm: function(){ return Promise.resolve({ok:true}); },
    exportStats: function(){ return Promise.resolve({ok:true}); },
  };
  window.addEventListener('pywebviewready', function(){
    if (window.pywebview && window.pywebview.api) {
      Object.assign(window.ApiBridge, window.pywebview.api);
      window.ApiBridge.ready = true;
      console.log('[bridge] pywebview api ready');
    }
  });
</script>
'''
anchor = "ReactDOM.createRoot(document.getElementById('root')).render(<App />);"
s = s.replace(anchor, bridge + anchor)

io.open(p, 'w', encoding='utf-8').write(s)
print('done; unpkg_left=', s.count('unpkg.com'), '; bug_left=', s.count('URL.createObjecturl(/api'))
