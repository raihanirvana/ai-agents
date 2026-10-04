import assert from "node:assert/strict";
import test from "node:test";
import { cartTotalCents } from "../src/cart.js";

test("cart total sums price times quantity", () => {
  assert.equal(cartTotalCents([{ id: "espresso", qty: 2 }, { id: "latte", qty: 1 }]), 900);
});

test("unknown item is rejected", () => {
  assert.throws(() => cartTotalCents([{ id: "tea", qty: 1 }]), /unknown menu item/);
});
