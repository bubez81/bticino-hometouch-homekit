'use strict';
// Copies the Python listener into the package before publishing, so one npm
// install brings everything. In a repository checkout the plugin uses ../src.
const fs = require('node:fs');
const path = require('node:path');
const target = path.join(__dirname, 'listener');
fs.rmSync(target, {recursive: true, force: true});
fs.mkdirSync(target);
const src = path.join(__dirname, '..', 'src');
for (const name of fs.readdirSync(src)) if (name.endsWith('.py')) fs.copyFileSync(path.join(src, name), path.join(target, name));
fs.copyFileSync(path.join(__dirname, '..', 'scripts', 'probe-camera.py'), path.join(target, 'probe-camera.py'));
console.log(`Bundled ${fs.readdirSync(target).length} listener files`);
