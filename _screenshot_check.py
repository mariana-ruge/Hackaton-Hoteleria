from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 480, "height": 950})
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.goto("http://localhost:5000")
    page.wait_for_timeout(3500)
    page.evaluate("""
        document.getElementById('splash').classList.add('oculto');
        document.getElementById('bienvenida').classList.add('oculto');
        document.getElementById('qrPantalla').classList.add('oculto');
        document.getElementById('toast').classList.remove('on');
        document.querySelectorAll('.vista').forEach(v => v.classList.remove('on'));
        document.getElementById('v-auditoria').classList.add('on');
        document.getElementById('listaAud').innerHTML = `
          <div class="aud grave">
            <div class="cab">
              <div><div class="nom">Arroz Doña Pepa</div>
                <div style="font-size:12px;color:var(--texto-secundario)">Bodega Central</div>
              </div>
              <span class="et et-mal">CRITICA</span>
            </div>
            <div class="cifras">
              <div><b>Sistema</b>120 kg</div>
              <div><b>Consenso</b>95 kg</div>
              <div><b>Diferencia</b><span style="color:var(--rojo)">-25</span></div>
              <div><b>Error</b>20.8 %</div>
            </div>
          </div>`;
    """)
    page.locator("#listaAud").screenshot(path="_shot_grave.png")

    browser.close()

print("console errors:", errors)
print("done")
