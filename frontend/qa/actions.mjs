// How a person gets each tab to its result, on the new UI. Shared by the screenshot, parity and axe scripts.
// `start` sends the request and returns at once. `done` waits for the finished result.

export const start = {
  async qa(page, inputs) {
    await page.fill("#chat-input", inputs.chat1);
    await page.locator('button[aria-label="পাঠান"]').click();
  },
  async predict(page, inputs) {
    await page.fill("#situation", inputs.predict);
    await page.getByRole("button", { name: "বিশ্লেষণ করুন" }).click();
  },
  async search(page, inputs) {
    await page.fill("#search-input", inputs.search);
    await page.getByRole("search").getByRole("button", { name: "খুঁজুন" }).click();
  },
  async docs(page, inputs) {
    await page.getByRole("button", { name: /ভাড়া চুক্তি/ }).click();
    await page.fill("#doc-details", inputs.docs);
    await page.getByRole("button", { name: "খসড়া তৈরি করুন" }).click();
  },
};

export const done = {
  qa: (page) => page.getByRole("article", { name: "উত্তর" }).getByText("প্রযোজ্য আইন").waitFor(),
  predict: (page) => page.getByRole("heading", { name: "খুঁজে পাওয়া রায়গুলোর ফল" }).waitFor(),
  search: (page) => page.getByRole("region", { name: "মামলা" }).waitFor(),
  docs: (page) => page.getByRole("article", { name: "তৈরি করা খসড়া" }).waitFor(),
};

export const act = Object.fromEntries(
  Object.keys(start).map((tab) => [
    tab,
    async (page, inputs) => {
      await start[tab](page, inputs);
      await done[tab](page);
    },
  ]),
);
