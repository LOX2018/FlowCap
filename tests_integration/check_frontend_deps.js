// 检查前端依赖与构建配置
const fs = require("fs");
const path = require("path");

const frontend = path.join(__dirname, "..", "frontend");
const nm = path.join(frontend, "node_modules");
const pkgPath = path.join(frontend, "package.json");

const out = [];
out.push("node_modules exists: " + fs.existsSync(nm));

const scripts = JSON.parse(fs.readFileSync(pkgPath, "utf8")).scripts;
out.push("scripts: " + JSON.stringify(scripts, null, 2));

// 检查关键依赖
const deps = ["react", "react-dom", "@tauri-apps/api", "vite", "typescript"];
for (const d of deps) {
  const p = path.join(nm, d, "package.json");
  out.push(`dep ${d}: ` + (fs.existsSync(p) ? "INSTALLED" : "MISSING"));
}

fs.writeFileSync(path.join(__dirname, "_fe_deps.txt"), out.join("\n"), "utf8");
console.log(out.join("\n"));
