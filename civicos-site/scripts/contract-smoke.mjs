import { readdir, readFile } from "node:fs/promises";

const files = await readdir("dist/assets");
const jsFiles = files.filter((file) => file.endsWith(".js"));
if (jsFiles.length === 0) {
  throw new Error("Vite build produced no JavaScript assets");
}

const bundle = (
  await Promise.all(jsFiles.map((file) => readFile(`dist/assets/${file}`, "utf8")))
).join("\n");
for (const route of [
  "/api/v1/consent/",
  "/api/v1/consent/schema/",
  "/api/v1/consent/docs/",
]) {
  if (!bundle.includes(route)) {
    throw new Error(`Missing documented API route: ${route}`);
  }
}
if (!bundle.includes("api.civicosbb.ca")) {
  throw new Error("Built frontend does not reference the documented API host");
}

console.log(`Frontend/API contract smoke passed (${jsFiles.length} asset(s))`);
