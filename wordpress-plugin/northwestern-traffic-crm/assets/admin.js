document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.nwtc-copy-input').forEach((input) => {
    input.addEventListener('focus', () => input.select());
  });
});
