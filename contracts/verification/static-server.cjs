const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = '/site';
const types = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml'};
http.createServer((req, res) => {
  try {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    let file = path.resolve(root, '.' + pathname);
    if (file !== root && !file.startsWith(root + '/')) throw Error('outside site');
    const checked = (p) => {
      let current = root;
      for (const part of path.relative(root, p).split('/').filter(Boolean)) {
        current = path.join(current, part);
        if (fs.lstatSync(current).isSymbolicLink()) throw Error('symlink');
      }
      return p;
    };
    checked(file);
    if (fs.statSync(file).isDirectory()) file = checked(path.join(file, 'index.html'));
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > 8 * 1024 * 1024) throw Error('not a bounded file');
    res.writeHead(200, {'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store'});
    if (req.method === 'HEAD') res.end(); else fs.createReadStream(file).pipe(res);
  } catch { res.writeHead(404); res.end(); }
}).listen(4173, '0.0.0.0');
