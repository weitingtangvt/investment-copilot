import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(__dirname, "..");
const files = [
  resolve(packageRoot, "node_modules/candleview/dist/index.mjs"),
  resolve(packageRoot, "node_modules/candleview/dist/index.js"),
];

const watermarkPatterns = [
  /,\s*new Rl\(\{\s*chartSeries:\s*this\.currentSeries,\s*chart:\s*this\.chart\s*\}\)\.addWatermark\(\{\s*src:\s*Bl,\s*size:\s*40,\s*opacity:\s*2,\s*offsetX:\s*20,\s*offsetY:\s*45\s*\}\)/g,
  /,\s*new \w+\(\{chartSeries:this\.currentSeries,chart:this\.chart\}\)\.addWatermark\(\{src:\w+,size:40,opacity:2,offsetX:20,offsetY:45\}\)/g,
];

const virtualPaddingPatterns = [
  [/virtualDataBeforeCount:\s*500,\s*virtualDataAfterCount:\s*500/g, "virtualDataBeforeCount:0,virtualDataAfterCount:0"],
  [/virtualDataBeforeCount:\s*500,\s*\n\s*virtualDataAfterCount:\s*500/g, "virtualDataBeforeCount: 0,\n      virtualDataAfterCount: 0"],
];

const maParamsCompact = "params:[{paramName:\"MA\",paramValue:5,lineColor:\"#FF6B6B\",lineWidth:1},{paramName:\"MA\",paramValue:10,lineColor:\"#6958ffff\",lineWidth:1},{paramName:\"MA\",paramValue:20,lineColor:\"#0ed3ffff\",lineWidth:1},{paramName:\"MA\",paramValue:30,lineColor:\"#3bf79fff\",lineWidth:1},{paramName:\"MA\",paramValue:60,lineColor:\"#f7c933ff\",lineWidth:1}]";
const maParamsCompactReplacement = "params:[{paramName:\"MA50\",paramValue:50,lineColor:\"#2563eb\",lineWidth:1.4},{paramName:\"MA100\",paramValue:100,lineColor:\"#f59e0b\",lineWidth:1.4},{paramName:\"MA200\",paramValue:200,lineColor:\"#0f766e\",lineWidth:1.4}]";

const maParamsPrettyPattern = /params:\s*\[\s*\{\s*paramName:\s*['"]MA['"],\s*paramValue:\s*5,\s*lineColor:\s*['"]#FF6B6B['"],\s*lineWidth:\s*1\s*\},\s*\{\s*paramName:\s*['"]MA['"],\s*paramValue:\s*10,\s*lineColor:\s*['"]#6958ffff['"],\s*lineWidth:\s*1\s*\},\s*\{\s*paramName:\s*['"]MA['"],\s*paramValue:\s*20,\s*lineColor:\s*['"]#0ed3ffff['"],\s*lineWidth:\s*1\s*\},\s*\{\s*paramName:\s*['"]MA['"],\s*paramValue:\s*30,\s*lineColor:\s*['"]#3bf79fff['"],\s*lineWidth:\s*1\s*\},\s*\{\s*paramName:\s*['"]MA['"],\s*paramValue:\s*60,\s*lineColor:\s*['"]#f7c933ff['"],\s*lineWidth:\s*1\s*\}\s*\]/g;
const maParamsPrettyReplacement = "params: [\n        { paramName: 'MA50', paramValue: 50, lineColor: '#2563eb', lineWidth: 1.4 },\n        { paramName: 'MA100', paramValue: 100, lineColor: '#f59e0b', lineWidth: 1.4 },\n        { paramName: 'MA200', paramValue: 200, lineColor: '#0f766e', lineWidth: 1.4 }\n    ]";

const timeScalePatterns = [
  [/timeScale:\s*\{\s*timeVisible:\s*!0,\s*secondsVisible:\s*!1,\s*borderColor:([^}]+)\}/g, "timeScale:{visible:!1,timeVisible:!0,secondsVisible:!1,borderColor:$1}"],
  [/timeScale:\s*\{\s*timeVisible:\s*true,\s*secondsVisible:\s*false,\s*borderColor:([^}]+)\}/g, "timeScale: { visible: false, timeVisible: true, secondsVisible: false, borderColor:$1 }"],
];

const fontPatterns = [
  [/layout:\s*theme\.layout,\s*grid:/g, "layout: { ...theme.layout, fontFamily: \"Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif\", fontSize: 12 },\n            grid:"],
  [/layout:([a-zA-Z_$][\\w$]*)\.layout,grid:/g, "layout:{...$1.layout,fontFamily:\"Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif\",fontSize:12},grid:"],
  [/layout:\s*currentTheme\.layout,\s*grid:/g, "layout: { ...currentTheme.layout, fontFamily: \"Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif\", fontSize: 12 },\n            grid:"],
  [/layout:([a-zA-Z_$][\\w$]*)\.layout,grid:/g, "layout:{...$1.layout,fontFamily:\"Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif\",fontSize:12},grid:"],
];

for (const file of files) {
  const source = readFileSync(file, "utf-8");
  let patched = source;
  const hadWatermark = source.includes("addWatermark({src:") || source.includes("addWatermark({\n          src:");
  const hadDefaultMa = source.includes(maParamsCompact) || maParamsPrettyPattern.test(source);
  maParamsPrettyPattern.lastIndex = 0;
  const hadVisibleTimeScale = timeScalePatterns.some(([pattern]) => pattern.test(source));
  timeScalePatterns.forEach(([pattern]) => { pattern.lastIndex = 0; });
  const hadPatchableLayout = fontPatterns.some(([pattern]) => pattern.test(source));
  fontPatterns.forEach(([pattern]) => { pattern.lastIndex = 0; });
  for (const pattern of watermarkPatterns) {
    patched = patched.replace(pattern, "");
  }
  for (const [pattern, replacement] of virtualPaddingPatterns) {
    patched = patched.replace(pattern, replacement);
  }
  patched = patched
    .replaceAll(maParamsCompact, maParamsCompactReplacement)
    .replace(maParamsPrettyPattern, maParamsPrettyReplacement);
  for (const [pattern, replacement] of timeScalePatterns) {
    patched = patched.replace(pattern, replacement);
  }
  for (const [pattern, replacement] of fontPatterns) {
    patched = patched.replace(pattern, replacement);
  }
  if (hadWatermark && (patched.includes("addWatermark({src:") || patched.includes("addWatermark({\n          src:"))) {
    throw new Error(`Unable to strip CandleView watermark from ${file}`);
  }
  if (hadDefaultMa && !patched.includes("MA50")) {
    throw new Error(`Unable to patch CandleView MA defaults in ${file}`);
  }
  if (hadVisibleTimeScale && !patched.includes("visible:!1,timeVisible") && !patched.includes("visible: false, timeVisible")) {
    throw new Error(`Unable to hide CandleView native time axis in ${file}`);
  }
  if (hadPatchableLayout && !patched.includes("fontFamily")) {
    throw new Error(`Unable to patch CandleView font family in ${file}`);
  }
  if (patched !== source) {
    writeFileSync(file, patched, "utf-8");
  }
}
