const { spawnSync } = require("node:child_process");

const env = {
  ...process.env,
  OPENAI_API_KEY: process.env.OPENAI_API_KEY || "test-openai-api-key",
};
const python = process.env.PYTHON || "python3";

const result = spawnSync(
  python,
  ["-m", "unittest", "discover", "-s", "tests", "-p", "test*.py"],
  {
    cwd: process.cwd(),
    env,
    stdio: "inherit",
  },
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status || 0);
