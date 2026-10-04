import React from "react";
import { createRoot } from "react-dom/client";
import { MENU, cartTotalCents } from "./cart.js";

function App() {
  const total = cartTotalCents([{ id: "latte", qty: 2 }]);
  return (
    <main>
      <h1>Coffee menu (reference target)</h1>
      <ul>{MENU.map((m) => <li key={m.id}>{m.name}: {(m.priceCents / 100).toFixed(2)}</li>)}</ul>
      <p>Sample cart total: {(total / 100).toFixed(2)}</p>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
