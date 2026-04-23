import { app } from "./app";

const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || "0.0.0.0";

const server = app.listen(port, host);

server.on("listening", () => {
  console.log(`Communication-first CRM listening on http://${host}:${port}`);
});

server.on("error", (error: NodeJS.ErrnoException) => {
  if (error.code === "EPERM" || error.code === "EACCES") {
    console.error(
      `Could not bind the server to ${host}:${port}. Run outside the restricted sandbox or set HOST=127.0.0.1 in environments that only allow loopback.`
    );
  } else if (error.code === "EADDRINUSE") {
    console.error(`Port ${port} is already in use on host ${host}.`);
  } else {
    console.error("Server failed to start.", error);
  }

  process.exit(1);
});
