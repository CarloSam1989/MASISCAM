const passwordInput = document.getElementById("id_password");
const togglePassword = document.getElementById("toggle-password");

if (passwordInput && togglePassword) {
    togglePassword.addEventListener("click", () => {
        const visible = passwordInput.type === "password";
        passwordInput.type = visible ? "text" : "password";
        togglePassword.setAttribute("aria-label", visible ? "Ocultar contraseña" : "Mostrar contraseña");
        togglePassword.classList.toggle("is-visible", visible);
    });
}