// index.js
const express = require("express");
const app = express();
const http = require("http");

app.get("/", (req, res) => {
  res.send("Pinger is alive");
});

app.listen(8080, () => {
  console.log("Pinger running on port 8080");
});

setInterval(() => {
  http.get("https://<your-heroku-app>.herokuapp.com");
}, 5 * 60 * 1000);
