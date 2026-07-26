from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()

    for name, w, h in [("mobile", 375, 900), ("desktop", 1440, 1000)]:
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto("http://localhost:5000")
        # neutralize the onboarding timer so it can't pop back mid-check
        page.evaluate("window.pasarSplash = function(){};")
        page.evaluate("""
            document.getElementById('splash').classList.add('oculto');
            document.getElementById('bienvenida').classList.add('oculto');
            document.getElementById('qrPantalla').classList.add('oculto');
            qrLeido({nombre:'Jenny Gutierrez', bodega:'Restaurante', documento:'123456'});
        """)
        page.wait_for_timeout(300)
        page.click("#btnBeneficioCerrar")
        page.wait_for_timeout(100)
        page.screenshot(path=f"_check_dash_{name}.png")
        page.close()

    browser.close()

print("done")
