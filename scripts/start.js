const { spawnSync } = require("node:child_process");

const port = process.env.PORT || "8000";
const python = process.env.PYTHON || "python3";

function run(args) {
  const result = spawnSync(python, args, {
    cwd: process.cwd(),
    env: process.env,
    stdio: "inherit",
  });

  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }

  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

run(["-m", "scripts.init_db"]);
run(["-m", "scripts.seed_demo"]);

const server = spawnSync(
  python,
  ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port],
  {
    cwd: process.cwd(),
    env: process.env,
    stdio: "inherit",
  },
);

if (server.error) {
  console.error(server.error.message);
  process.exit(1);
}

process.exit(server.status || 0);
