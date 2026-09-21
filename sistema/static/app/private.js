window.masiscamEnhanceForms = container => {
  container.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="submit"]), textarea').forEach(element => element.classList.add('form-control'));
  container.querySelectorAll('select').forEach(element => { element.classList.remove('form-control'); element.classList.add('form-select'); });
  container.querySelectorAll('input[type="checkbox"], input[type="radio"]').forEach(element => element.classList.add('form-check-input'));
};
window.masiscamEnhanceForms(document);
