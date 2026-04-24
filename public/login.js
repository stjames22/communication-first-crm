const form = document.querySelector("#login-form");
const passwordInput = document.querySelector("#password");
const errorBox = document.querySelector("#login-error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.textContent = "";

  const submitButton = form.querySelector("button");
  submitButton.disabled = true;

  try {
    const response = await fetch("/auth/password-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: passwordInput.value })
    });

    if (!response.ok) {
      errorBox.textContent = "That password did not work.";
      submitButton.disabled = false;
      return;
    }

    const params = new URLSearchParams(window.location.search);
    window.location.href = params.get("next") || "/";
  } catch {
    errorBox.textContent = "Could not reach the CRM. Try again in a moment.";
    submitButton.disabled = false;
  }
});
