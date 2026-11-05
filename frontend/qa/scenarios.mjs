// The same user actions, expressed for the old page and the new one, so their request payloads can be diffed.
// Every action uses made-up inputs. Requests are stubbed, so no LLM call is made.

export const INPUTS = {
  chat1: "চেক ডিজঅনার হলে কী করব?",
  chat2: "নোটিশ কীভাবে পাঠাব?",
  predict: "আমার ভাড়াটিয়া ছয় মাস ধরে ভাড়া দিচ্ছে না, আমি মামলা করতে চাই।",
  search: "dishonour of cheque section 138",
  docs: "Landlord: Test Person A, Dhaka. Tenant: Test Person B. Monthly rent 10,000 taka, for 12 months.",
};

// The scenarios, by name. Each returns after its requests have been sent.
export const SCENARIOS = [
  "chat_first_turn",
  "chat_second_turn_has_history",
  "chat_with_filters",
  "chat_follow_up_option",
  "predict_auto_detect",
  "predict_with_case_type",
  "search_both_no_filters",
  "search_cases_with_filters",
  "search_laws_only",
  "docs_rent",
];
