/* ============================================================
   INVENTARIO 360 · COLSUBSIDIO
   Lógica de interfaz: navegación por pestañas, carga y limpieza
   de archivos, sesión de conteo con dictado y auditoría.
   Consume la API expuesta por backend/server.py.
   ============================================================ */
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const S={catalogo:null,reporteCarga:null,correccionesCarga:[],sesion:null,contadores:[],auditor:'',turno:0,bloqueo:null,filtroDatos:'productos',filtroBodegas:'todas',bodegaSelId:null,detalleLimpiezaAbierto:false};

function toast(m,t=''){const e=$('#toast');e.textContent=m;e.className='on '+t;
  clearTimeout(e._t);e._t=setTimeout(()=>e.className='',3400);}
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const iniciales=nombre=>(String(nombre||'').trim().split(/\s+/).slice(0,2)
  .map(p=>p[0]?.toUpperCase()||'').join('')||'?');

async function api(url,opt={}){
  const r=await fetch(url,opt);
  let d; try{d=await r.json()}catch{throw new Error('Respuesta no válida del servidor.')}
  return d;
}

/* ── Splash: primera pantalla, pasa sola a los 2.6s o al tocarla ── */
let splashTimer=setTimeout(pasarSplash,2600);
function pasarSplash(){
  clearTimeout(splashTimer);
  $('#splash').classList.add('oculto');
  $('#bienvenida').classList.remove('oculto');
}
$('#splash').onclick=pasarSplash;

/* ── Bienvenida ── */
function cerrarBienvenida(){$('#bienvenida').classList.add('oculto')}
function abrirBienvenida(){
  clearTimeout(splashTimer);
  detenerCamaraQr();
  $('#splash').classList.add('oculto');
  $('#qrPantalla').classList.add('oculto');
  $('#bienvenida').classList.remove('oculto');
}
$('#btnInicio').onclick=abrirBienvenida;

/* "Código QR" abre la cámara real del dispositivo y lee el código
   con el detector de OpenCV en el backend. */
   $('#btnBienvenidaCargar').onclick=()=>{
    cerrarBienvenida();
  $('#qrPantalla').classList.remove('oculto');
  iniciarCamaraQr();
  };
$('#btnQrVolver').onclick=()=>{
  detenerCamaraQr();
  $('#qrPantalla').classList.add('oculto');
  $('#bienvenida').classList.remove('oculto');
};
$('#btnQrEscanear').onclick=()=>intentarEscaneoQr(false);

/* ── Cámara + lectura de QR ── */
let qrStream=null, qrAutoTimer=null, qrOcupado=false;

function fijarEstadoQr(msg,tipo){
  const e=$('#qrEstado');e.textContent=msg||'';e.className='qr-estado'+(tipo?' '+tipo:'');
}

async function iniciarCamaraQr(){
  fijarEstadoQr('');
  if(!navigator.mediaDevices?.getUserMedia){
    fijarEstadoQr('Este navegador no permite acceder a la cámara.','mal');
    return;
  }
  try{
    qrStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:false});
    const video=$('#qrVideo');
    video.srcObject=qrStream;
    await video.play();
    clearInterval(qrAutoTimer);
    qrAutoTimer=setInterval(()=>intentarEscaneoQr(true),900);
  }catch(e){
    fijarEstadoQr('No se pudo acceder a la cámara. Revisa los permisos del navegador.','mal');
  }
}

function detenerCamaraQr(){
  clearInterval(qrAutoTimer);qrAutoTimer=null;
  if(qrStream){qrStream.getTracks().forEach(t=>t.stop());qrStream=null;}
  $('#qrVideo').srcObject=null;
}

async function intentarEscaneoQr(silencioso){
  const video=$('#qrVideo');
  if(qrOcupado||!video.videoWidth)return;
  qrOcupado=true;
  if(!silencioso)fijarEstadoQr('Leyendo…');
  try{
    const canvas=$('#qrCanvas');
    canvas.width=video.videoWidth;canvas.height=video.videoHeight;
    canvas.getContext('2d').drawImage(video,0,0);
    const imagen=canvas.toDataURL('image/jpeg',0.85);
    const d=await api('/api/qr/decodificar',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({imagen})});
      if(d.ok && d.perfil){
        qrLeido(d.perfil);
      }else if(!silencioso){
        fijarEstadoQr(
          d.error || 'No se detectó ningún código QR. Acércate o mejora la luz.',
          'mal'
        );
      }
  }catch(e){
    if(!silencioso)fijarEstadoQr('Error leyendo la cámara.','mal');
  }finally{
    qrOcupado=false;
  }
}

function qrLeido(perfil){
  detenerCamaraQr();

  fijarEstadoQr('Código leído correctamente.', 'bien');
  $('#qrPantalla').classList.add('oculto');

  pintarPerfil(perfil);

  $('#avatar').textContent = iniciales(perfil.nombre);

  $('#perfilNombre').textContent =
    perfil.nombre || 'Inventario 360';

  $('#perfilSub').textContent = [
    perfil.bodega,
    perfil.documento ? `ID ${perfil.documento}` : ''
  ].filter(Boolean).join(' · ');

  toast(
    `${perfil.nombre} · ${perfil.bodega} · ID ${perfil.documento}`,
    'bien'
  );

  ir('perfil');
}

/* ── Navegación ── */
function ir(v){
  $$('.tab').forEach(t=>t.setAttribute('aria-selected',t.dataset.v===v));
  $$('.vista').forEach(s=>s.classList.toggle('on',s.id==='v-'+v));
  if(v==='auditoria')pintarAuditoria();
  if(v === 'perfil'){cargarPerfil();}
  window.scrollTo({top:0,behavior:'smooth'});
}
$$('.tab').forEach(t=>t.onclick=()=>{if(!t.disabled){ir(t.dataset.v);t.blur()}});
const habilitar=v=>$$(`.tab[data-v="${v}"]`).forEach(t=>t.disabled=false);

/* ══════════ 1 · CARGA ══════════ */
const drop=$('#drop'),inputArchivo=$('#archivo');
drop.onclick=()=>inputArchivo.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('activo')};
drop.ondragleave=()=>drop.classList.remove('activo');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('activo');
  if(e.dataTransfer.files[0])subir(e.dataTransfer.files[0])};
inputArchivo.onchange=e=>{if(e.target.files[0])subir(e.target.files[0])};

async function subir(file){
  const modo=$('input[name=modo]:checked').value;
  $('#resDatos').innerHTML='<div class="card"><span class="spin"></span>Procesando '
    +esc(file.name)+'…</div>';
  const fd=new FormData();fd.append('archivo',file);fd.append('modo',modo);
  try{
    const d=await api('/api/cargar',{method:'POST',body:fd});
    if(!d.ok){$('#resDatos').innerHTML=
      `<div class="aviso mal"><b>No se pudo cargar.</b><br>${esc(d.error)}</div>`;return;}
    d.modo==='bodegas'?pintarBodegas(d):pintarCatalogo(d);
  }catch(e){
    $('#resDatos').innerHTML=`<div class="aviso mal">${esc(e.message)}</div>`;
  }
}

function met(v,k,cls='',filtro=''){return `<button type="button" class="met ${cls} ${filtro?'clickable':''}"
  ${filtro?`data-filtro="${esc(filtro)}"`:''}><div class="v">${v}</div>
  <div class="k">${k}</div></button>`}

function metCompacta(v,k,cls=''){return `<div class="met compacta ${cls}"><div class="v">${v}</div><div class="k">${k}</div></div>`}

function renderContextoBodega(nombre=''){
  const host=$('#ctxBodega');
  const filas=(S.catalogo||[]).filter(x=>String(x.bodega||'').toLowerCase()===String(nombre||'').toLowerCase());
  if(!nombre||!filas.length){
    host.innerHTML=`<h3>Contexto de la bodega</h3><div class="vacio-msg" style="padding:28px 20px">
      <div class="ico">
    <img src="/static/img/bodega.svg" alt="Bodega">
      </div><b>Selecciona una bodega</b>
      <span>Verás cuántas referencias y existencias históricas se usarán para validar el conteo.</span>
    </div>`;
    return;
  }
  const sinStock=filas.filter(x=>String(x.estado_stock)==='Sin Stock').length;
  const conStock=filas.length-sinStock;
  const referencias=filas.filter(x=>String(x.codigo||'').trim()).length;
  const unidades=new Set(filas.map(x=>String(x.unidad||'').trim()).filter(Boolean)).size;
  host.innerHTML=`<h3>Contexto de la bodega</h3>
    <p class="sub" style="margin-bottom:14px;max-width:none">La validación en tiempo real comparará cada dictado con el histórico cargado de <b>${esc(nombre)}</b>.</p>
    <div class="mets">
      ${metCompacta(filas.length,'Productos históricos','bien')}
      ${metCompacta(conStock,'Con stock','bien')}
      ${metCompacta(sinStock,'Sin stock',sinStock?'alerta':'')}
      ${metCompacta(referencias,'Con código')}
      ${metCompacta(unidades,'Unidades')}
    </div>
    <div class="aviso bien"><b>Cómo ayuda esta capa:</b><br>cuando el dictado no coincide con la unidad o se desvía fuerte frente al histórico de esta bodega, el sistema bloquea, pide confirmación o envía a auditoría.</div>`;
}

function filasFiltradasDatos(){
  const filas=S.catalogo||[], r=S.reporteCarga||{}, f=S.filtroDatos;
  if(f==='productos')return filas;
  if(f==='unidades_corregidas')return filas.filter(x=>String(x.observaciones||'').toLowerCase().includes('unidad'));
  if(f==='unidades_desconocidas')return filas.filter(x=>String(x.observaciones||'').toLowerCase().includes('no reconocida'));
  if(f==='negativos')return [];
  if(f==='duplicados')return [];
  if(f==='descartadas')return [];
  return filas;
}

function detalleFiltroDatos(){
  const r=S.reporteCarga||{}, f=S.filtroDatos;
  if(f==='negativos')return {titulo:'Valores negativos eliminados',items:r.valores_negativos||[]};
  if(f==='duplicados')return {titulo:'Duplicados eliminados',items:r.duplicados_detalle||[]};
  if(f==='descartadas')return {titulo:'Filas descartadas',items:r.filas_descartadas||[]};
  if(f==='unidades_corregidas')return {
    titulo:'Productos con unidades normalizadas',
    items:(S.catalogo||[]).filter(x=>String(x.observaciones||'').toLowerCase().includes('unidad'))
  };
  if(f==='unidades_desconocidas')return {
    titulo:'Productos clasificados con unidad desconocida',
    items:(S.catalogo||[]).filter(x=>String(x.observaciones||'').toLowerCase().includes('no reconocida'))
  };
  return null;
}

function renderDetalleFiltro(){
  const d=detalleFiltroDatos();
  if(!d||S.filtroDatos==='productos')return '';
  const items=d.items||[];
  return `<div class="card"><h3>${esc(d.titulo)}</h3>
    ${items.length?`<div style="max-height:220px;overflow-y:auto;font-size:13px">${items.map(x=>`<div style="padding:8px 0;border-bottom:1px solid var(--gris-borde)">
      <span class="mono">${esc(x.codigo||'Sin código')}</span>${x.hoja?` <span class="et et-info">${esc(x.hoja)}</span>`:''}
      ${x.producto?` · <b>${esc(x.producto)}</b>`:''}
      <div style="color:var(--texto-secundario)">${esc(x.bodega||'Sin bodega')}${x.unidad?` · ${esc(x.unidad)}`:''}${x.valor_original!=null?` · valor original: ${esc(x.valor_original)}`:''}${x.causa?` · ${esc(x.causa)}`:''}${x.observaciones?` · ${esc(x.observaciones)}`:''}</div>
    </div>`).join('')}</div>`:'<div style="color:var(--texto-secundario)">No hay elementos para este filtro.</div>'}
  </div>`;
}

function activarFiltroDatos(filtro){
  S.filtroDatos=filtro;
  pintarCatalogo({reporte:S.reporteCarga,filas:S.catalogo,correcciones:S.correccionesCarga,bodegas:S.bodegasCarga,_sinToast:true});
}

function pintarCatalogo(d){
  const r=d.reporte;
  S.catalogo=d.filas;
  S.reporteCarga=r;
  S.correccionesCarga=d.correcciones||[];
  S.bodegasCarga=d.bodegas||S.bodegasCarga||[];
  const chipCatalogo = $('#chipCat');
  const textoCatalogo = chipCatalogo.querySelector('.chip-text');

  textoCatalogo.textContent = `${r.filas_final} productos`;
  chipCatalogo.classList.add('on');

  const sel=$('#selBodega');
  const bodegaPrevia=sel.value;
  sel.innerHTML='<option value="">Selecciona una bodega</option>'+
    d.bodegas.map(b=>`<option value="${esc(b)}">${esc(b)}</option>`).join('');
  if(bodegaPrevia&&d.bodegas.includes(bodegaPrevia))sel.value=bodegaPrevia;
  renderContextoBodega(sel.value);

  const filas=filasFiltradasDatos().map(f=>`<tr>
    <td class="mono" style="color:var(--texto-secundario)">${esc(f.codigo)}</td>
    <td>${esc(f.producto)}</td>
    <td style="color:var(--texto-secundario);font-size:12px">${esc(f.bodega)}</td>
    <td><span class="et et-info">${esc(f.unidad)}</span>
        ${f.unidad_original&&f.unidad_original!==f.unidad
          ?`<div style="font-size:10px;color:var(--texto-secundario);margin-top:3px"
             class="mono">antes: ${esc(f.unidad_original)}</div>`:''}</td>
    <td class="num">${Number(f.stock_disponible).toLocaleString('es-CO')}</td>
    <td><span class="et ${f.estado_stock==='OK'?'et-ok':'et-gris'}">
        ${esc(f.estado_stock)}</span></td>
    <td style="font-size:11px;color:var(--texto-secundario);max-width:280px">
        ${esc(f.observaciones)}</td></tr>`).join('');

  $('#resDatos').innerHTML=`
    <div class="card exito">
      <div class="ico-check">✓</div>
      <div class="tit">Tu archivo está listo para la validación</div>
      <p class="sub">${r.filas_final} productos procesados y normalizados automáticamente por el sistema.</p>
      <div class="fila" style="margin-top:16px;justify-content:center">
        <button class="b-pri" onclick="ir('sesion')">Continuar</button>
        <button class="b-sec" type="button" id="btnVerDetalleLimpieza">${S.detalleLimpiezaAbierto
          ?'Ocultar detalle':'Ver detalle'}</button>
        <button class="b-sec" onclick="location.href='/api/exportar/catalogo'">Descargar</button>
      </div>
    </div>
    <div id="detalleLimpieza" class="${S.detalleLimpiezaAbierto?'':'oculto'}">
    <div class="mets">
      ${met(r.filas_final,'Productos','bien','productos')}
      ${met(r.negativos_corregidos,'Negativos eliminados',
        r.negativos_corregidos?'alerta':'','negativos')}
      ${met(r.unidades_corregidas,'Unidades normalizadas','','unidades_corregidas')}
      ${met(r.unidades_desconocidas,'Unidad desconocida',
        r.unidades_desconocidas?'mal':'','unidades_desconocidas')}
      ${met(r.duplicados,'Duplicados','','duplicados')}
      ${met(r.descartadas,'Filas descartadas','','descartadas')}
    </div>
    ${r.coherencia_modelo?`<div class="card"><h3>Coherencia de datos impulsada por analítica</h3>
      <div class="mets">
        ${met(r.coherencia_modelo.registros_analizados||0,'Registros analizados','bien')}
        ${met(r.coherencia_modelo.alertas||0,'Alertas estadísticas',(r.coherencia_modelo.alertas||0)?'alerta':'')}
        ${met(r.coherencia_modelo.criticos||0,'Críticos estadísticos',(r.coherencia_modelo.criticos||0)?'mal':'')}
      </div>
      <div class="aviso">Se aplicó un score de coherencia basado en mediana, IQR y MAD sobre artículos comparables entre bodegas para señalar cantidades atípicas antes del conteo.</div>
      ${r.coherencia_modelo.detalle?.length?`<div style="max-height:220px;overflow-y:auto;font-size:13px">${r.coherencia_modelo.detalle.slice(0,60).map(x=>`<div style="padding:8px 0;border-bottom:1px solid var(--gris-borde)">
        <span class="mono">${esc(x.codigo||'Sin código')}</span> · <b>${esc(x.producto)}</b>
        <div style="color:var(--texto-secundario)">${esc(x.bodega)} · ${esc(x.clasificacion)} · score ${esc(x.score)} · ${esc(x.detalle)}</div>
      </div>`).join('')}</div>`:''}
    </div>`:''}
        <div class="card"><h3>Filtro activo</h3><div class="fila">
      <span class="chip mono" style="border-color:var(--amarillo);color:#c98300">${esc({productos:'Todos los productos',negativos:'Negativos eliminados',unidades_corregidas:'Unidades normalizadas',unidades_desconocidas:'Unidad desconocida',duplicados:'Duplicados',descartadas:'Filas descartadas'}[S.filtroDatos]||'Todos los productos')}</span>
      ${S.filtroDatos!=='productos'?'<button class="b-sec" type="button" onclick="activarFiltroDatos(\'productos\')">Quitar filtro</button>':''}
        </div></div>
    ${r.advertencias.length?`<div class="aviso"><b>Revisa esto:</b><br>`+
      r.advertencias.map(esc).join('<br>')+`</div>`:''}
    <div class="card">
      <h3>Columnas detectadas</h3>
      <div class="fila">${Object.entries(r.columnas_detectadas).map(([o,c])=>
        `<span class="chip mono">${esc(o)} → ${esc(c)}</span>`).join('')||
        '<span style="color:var(--texto-secundario)">Ninguna</span>'}</div>
    </div>
    ${r.columnas_confusas?.length?`<div class="card"><h3>Columnas confusas y limpieza aplicada</h3>
      <div style="max-height:210px;overflow-y:auto;font-size:13px">
      ${r.columnas_confusas.map(c=>`<div style="padding:8px 0;border-bottom:1px solid var(--gris-borde)">
        <b>${esc(c.original)}</b>${c.hoja?` <span class="et et-info">${esc(c.hoja)}</span>`:''}
        <div style="color:var(--texto-secundario)">${esc(c.motivo)}</div>
      </div>`).join('')}</div></div>`:''}
    ${r.articulos_completados?.length||r.articulos_no_encontrados?.length?`<div class="card">
      <h3>Trazabilidad de números de artículo</h3>
      ${r.articulos_completados?.length?`<div style="margin-bottom:14px"><b>Completados desde otra hoja (${r.articulos_completados.length})</b>
        <div style="max-height:180px;overflow-y:auto;font-size:13px;margin-top:8px">
        ${r.articulos_completados.map(a=>`<div style="padding:8px 0;border-bottom:1px solid var(--gris-borde)">
          <span class="mono">${esc(a.codigo)}</span> · <b>${esc(a.producto)}</b>
          <div style="color:var(--texto-secundario)">${esc(a.detalle)}</div>
        </div>`).join('')}</div></div>`:''}
      ${r.articulos_no_encontrados?.length?`<div><b>No encontrados en otra hoja (${r.articulos_no_encontrados.length})</b>
        <div style="max-height:180px;overflow-y:auto;font-size:13px;margin-top:8px">
        ${r.articulos_no_encontrados.map(a=>`<div style="padding:8px 0;border-bottom:1px solid var(--gris-borde)">
          <span class="mono">${esc(a.codigo)}</span>${a.hoja?` <span class="et et-warn">${esc(a.hoja)}</span>`:''}
          <div style="color:var(--texto-secundario)">${esc(a.causa)}</div>
        </div>`).join('')}</div></div>`:''}
    </div>`:''}
    ${r.valores_negativos?.length?`<div class="card"><h3>Valores negativos eliminados</h3>
      <div style="max-height:220px;overflow-y:auto;font-size:13px">
      ${r.valores_negativos.map(n=>`<div style="padding:8px 0;border-bottom:1px solid var(--gris-borde)">
        <span class="mono">${esc(n.codigo||'Sin código')}</span> · <b>${esc(n.producto||'Sin producto')}</b>
        <div style="color:var(--texto-secundario)">${esc(n.bodega||'Sin bodega')} · valor original: ${esc(n.valor_original)} · ${esc(n.causa)}</div>
      </div>`).join('')}</div></div>`:''}
    ${renderDetalleFiltro()}
    ${d.correcciones.length?`<div class="card"><h3>Correcciones aplicadas
      (${d.correcciones.length})</h3>
      <div style="max-height:190px;overflow-y:auto;font-size:13px">
      ${d.correcciones.map(c=>`<div style="padding:6px 0;
        border-bottom:1px solid var(--gris-borde)"><b>${esc(c.producto)}</b><br>
        <span style="color:var(--texto-secundario)">${esc(c.detalle)}</span></div>`).join('')}
      </div></div>`:''}
    <div class="card">
      <h3>Catálogo normalizado</h3>
      <div class="tabla-wrap" style="max-height:440px;overflow-y:auto">
        <table><thead><tr><th>Código</th><th>Producto</th><th>Bodega</th>
          <th>Unidad</th><th>Stock</th><th>Estado</th><th>Observaciones</th>
        </tr></thead><tbody>${filas}</tbody></table>
      </div>
    </div>
    </div>`;
  $$('.met[data-filtro]').forEach(el=>{
    el.classList.toggle('activa',el.dataset.filtro===S.filtroDatos);
    el.onclick=()=>activarFiltroDatos(el.dataset.filtro);
  });
  $('#btnVerDetalleLimpieza').onclick=toggleDetalleLimpieza;
  habilitar('sesion');
  if(!d._sinToast)toast('Tu archivo está listo para la validación','bien');
}

function toggleDetalleLimpieza(){
  const det=$('#detalleLimpieza');
  if(!det)return;
  S.detalleLimpiezaAbierto=!S.detalleLimpiezaAbierto;
  det.classList.toggle('oculto',!S.detalleLimpiezaAbierto);
  $('#btnVerDetalleLimpieza').textContent=S.detalleLimpiezaAbierto
    ?'Ocultar detalle':'Ver detalle';
}

$('#selBodega').onchange=e=>renderContextoBodega(e.target.value);

function renderDetalleBodega(f){
  const variantes=String(f.variantes||'').split(' | ').filter(Boolean);
  const items=[{txt:f.bodega_original,tipo:'original'},
    ...variantes.map(v=>({txt:v,tipo:'variante'}))];
  return `<div class="aviso" style="margin-top:14px">
    <b>Registros originales agrupados en «${esc(f.bodega)}» (${items.length})</b>
    <div style="margin-top:10px;display:flex;flex-direction:column;gap:6px">
      ${items.map(x=>`<div class="mono" style="font-size:13px;padding:7px 10px;
        background:var(--gris-claro);border:1px solid var(--gris-borde);border-radius:var(--radio-md)">
        <span class="et ${x.tipo==='original'?'et-info':'et-warn'}"
          style="margin-right:9px">${x.tipo==='original'?'primer registro':'variante'}</span>
        ${esc(x.txt)}</div>`).join('')}
    </div>
  </div>`;
}

function seleccionarBodega(id){
  S.bodegaSelId=S.bodegaSelId===id?null:id;
  pintarBodegas(S.datosBodegas,true);
}
window.seleccionarBodega=seleccionarBodega;

function filasFiltradasBodegas(filas){
  const f=S.filtroBodegas;
  if(f==='duplicados')return filas.filter(x=>String(x.variantes||'').trim());
  return filas;
}

function renderDetalleFiltroBodegas(r){
  const f=S.filtroBodegas;
  if(f==='correcciones')return`<div class="card"><h3>Nombres corregidos (${(r.correcciones||[]).length})</h3>
    <div style="max-height:220px;overflow-y:auto;font-size:13px">
    ${(r.correcciones||[]).map(c=>`<div style="padding:7px 0;border-bottom:1px solid var(--gris-borde)" class="mono">
      ${esc(c.original)} <span style="color:var(--texto-secundario)">→</span> ${esc(c.normalizado)}
      </div>`).join('')||'<div style="color:var(--texto-secundario)">Sin correcciones.</div>'}</div></div>`;
  if(f==='revisar')return`<div class="card"><h3>Posibles duplicados · revisión manual (${(r.posibles_duplicados||[]).length})</h3>
    <div style="max-height:220px;overflow-y:auto">
    ${(r.posibles_duplicados||[]).map(x=>`<div style="padding:7px 0;
      border-bottom:1px solid var(--gris-borde);font-size:13px" class="mono">
      ${esc(x.a)} <span style="color:var(--texto-secundario)">≈</span> ${esc(x.b)}
      <span class="et et-warn" style="margin-left:8px">${x.similitud}</span>
      </div>`).join('')||'<div style="color:var(--texto-secundario)">Sin coincidencias por revisar.</div>'}</div></div>`;
  return'';
}

function activarFiltroBodegas(filtro){
  S.filtroBodegas=filtro;
  pintarBodegas(S.datosBodegas,true);
}
window.activarFiltroBodegas=activarFiltroBodegas;

function pintarBodegas(d,silencioso){
  S.datosBodegas=d;
  const r=d.reporte;
  const filas=filasFiltradasBodegas(d.filas||[]);
  const filaSel=(d.filas||[]).find(f=>f.id===S.bodegaSelId);
  const etiquetas={todas:'Todas las bodegas',duplicados:'Con duplicados',
    correcciones:'Nombres corregidos',revisar:'Posibles duplicados'};
  $('#resDatos').innerHTML=`
    ${d.autodetectado?`<div class="aviso"><b>Tipo detectado automáticamente.</b><br>
      ${esc(d.mensaje||'El archivo se procesó como maestro de bodegas.')}</div>`:''}
    <div class="mets">
      ${met(r.bodegas_unicas,'Bodegas únicas','bien','todas')}
      ${met(r.duplicados_exactos,'Duplicados',r.duplicados_exactos?'alerta':'','duplicados')}
      ${met(r.correcciones,'Nombres corregidos','','correcciones')}
      ${met(r.posibles_duplicados.length,'Por revisar',
            r.posibles_duplicados.length?'alerta':'','revisar')}
    </div>
    <div class="card"><h3>Filtro activo</h3><div class="fila">
      <span class="chip mono" style="border-color:var(--amarillo);color:#c98300">${esc(etiquetas[S.filtroBodegas]||'Todas las bodegas')}</span>
      ${S.filtroBodegas!=='todas'?'<button class="b-sec" type="button" onclick="activarFiltroBodegas(\'todas\')">Quitar filtro</button>':''}
    </div></div>
    ${renderDetalleFiltroBodegas(r)}
    <div class="card"><h3>Bodegas normalizadas</h3>
      <p class="sub" style="margin-bottom:12px;max-width:none">Toca una bodega para ver el listado de registros originales que se agruparon en ella.</p>
      <div class="tabla-wrap" style="max-height:440px;overflow-y:auto">
      <table><thead><tr><th>#</th><th>Bodega</th><th>Como venía</th>
        <th>Variantes</th></tr></thead><tbody>
      ${filas.map(f=>{
        const nVariantes=String(f.variantes||'').split(' | ').filter(Boolean).length;
        return `<tr class="fila-clic ${f.id===S.bodegaSelId?'activa-fila':''}"
          onclick="seleccionarBodega(${f.id})">
        <td class="mono" style="color:var(--texto-secundario)">${f.id}</td>
        <td>${esc(f.bodega)}</td>
        <td style="color:var(--texto-secundario);font-size:12px">${esc(f.bodega_original)}</td>
        <td style="color:var(--texto-secundario);font-size:12px">${nVariantes
          ?`<span class="et et-warn">${nVariantes} variante${nVariantes>1?'s':''}</span>`
          :'<span class="et et-gris">sin variantes</span>'}</td>
        </tr>`;
      }).join('')||'<tr><td colspan="4" style="color:var(--texto-secundario);text-align:center;padding:20px">Nada para este filtro.</td></tr>'}</tbody></table></div>
      ${filaSel?renderDetalleBodega(filaSel):''}
      <div class="fila" style="margin-top:14px">
        <button class="b-sec" onclick="location.href='/api/exportar/bodegas'">
          Descargar</button></div>
    </div>`;
  $$('.met[data-filtro]').forEach(el=>{
    el.classList.toggle('activa',el.dataset.filtro===S.filtroBodegas);
    el.onclick=()=>activarFiltroBodegas(el.dataset.filtro);
  });
  if(!silencioso)toast(d.autodetectado
    ? 'Archivo detectado como maestro de bodegas'
    : `${r.bodegas_unicas} bodegas normalizadas`,'bien');
}

/* ══════════ 2 · SESIÓN ══════════ */
$('#btnSesion').onclick=async()=>{
  const contadores=[$('#c1').value,$('#c2').value,$('#c3').value]
    .map(x=>x.trim()).filter(Boolean);
  const auditor=$('#nAud').value.trim();
  const bodega=$('#selBodega').value;
  if(!bodega)return toast('Selecciona una bodega para abrir la toma física.','mal');
  if(!contadores.length)return toast('Escribe al menos un contador.','mal');
  if(!auditor)return toast('La auditoría es obligatoria: falta el auditor.','mal');

  const d=await api('/api/sesion',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({bodega,contadores,auditor})});

  if(!d.ok){$('#resSesion').innerHTML=
    `<div class="aviso mal">${esc(d.error)}</div>`;return;}

  S.sesion=d.sesion;S.contadores=contadores;S.auditor=auditor;S.turno=0;
  const chipSesion = $('#chipSes');
  const textoSesion = chipSesion.querySelector('.chip-text');
  textoSesion.textContent = 'Sesión ' + d.sesion;
  chipSesion.classList.add('on');
  $('#quien').textContent=contadores[0];
  $('#avatar').textContent=iniciales(auditor);
  $('#perfilNombre').textContent=auditor;
  $('#perfilSub').textContent='Auditor · '+d.resumen.bodega;
  $('#resSesion').innerHTML=`<div class="aviso bien">
    <b>Sesión ${esc(d.sesion)} abierta.</b><br>
    ${esc(d.resumen.modalidad)} · Bodega:
    ${esc(d.resumen.bodega)} · Auditor: ${esc(auditor)}</div>
    ${d.contexto_bodega?`<div class="card" style="margin-top:14px"><h3>Base histórica usada en esta sesión</h3>
      <div class="mets">
        ${metCompacta(d.contexto_bodega.productos,'Productos','bien')}
        ${metCompacta(d.contexto_bodega.con_stock,'Con stock','bien')}
        ${metCompacta(d.contexto_bodega.sin_stock,'Sin stock',d.contexto_bodega.sin_stock?'alerta':'')}
        ${metCompacta(d.contexto_bodega.referencias,'Con código')}
        ${metCompacta(d.contexto_bodega.unidades,'Unidades')}
      </div>
      <div class="aviso" style="margin-top:14px">Las alertas, bloqueos y desvíos se calculan contra el histórico cargado de esta bodega.</div>
    </div>`:''}`;
  habilitar('conteo');habilitar('auditoria');
  ir('conteo');$('#dictado').focus();
  toast('Sesión '+d.sesion+' abierta','bien');
};

/* ══════════ 3 · CONTEO ══════════ */
let tv=null;
$('#dictado').oninput=()=>{clearTimeout(tv);tv=setTimeout(previa,260)};
$('#dictado').onkeydown=e=>{if(e.key==='Enter')$('#btnRegistrar').click()};

async function previa(){
  const t=$('#dictado').value.trim();
  if(!t){['vpP','vpU','vpC','vpS'].forEach(i=>{
    $('#'+i).textContent='—';$('#'+i).className='v'});
    $('#vp').classList.add('oculto');
    $('#interpretacionTitulo').classList.add('oculto');
    return;
  }
  const d=await api('/api/interpretar',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({texto:t,bodega:$('#selBodega').value})});
  if($('#dictado').value.trim()!==t)return;
  $('#vp').classList.remove('oculto');
  $('#interpretacionTitulo').classList.remove('oculto');
  const k=d.dictado;
  const set=(id,val)=>{const e=$('#'+id);
    e.textContent=val??'—';e.className='v'+(val?'':' vacio')};
  set('vpP',k.producto);
  set('vpU',k.unidad?`${k.unidad} (${esc(k.unidad_dictada||'')})`:null);
  set('vpC',k.cantidad);
  if(d.coincidencia){
    const c=d.coincidencia;
    $('#vpS').textContent=`${c.stock_disponible} ${c.unidad}`;
    $('#vpS').className='v';
  }else{$('#vpS').textContent='no encontrado';$('#vpS').className='v vacio';}
}

$('#btnLimpiar').onclick=()=>{$('#dictado').value='';previa();
  $('#resConteo').innerHTML='';S.bloqueo=null;$('#dictado').focus()};

$('#btnRegistrar').onclick=async()=>{
  const texto=$('#dictado').value.trim();
  if(!texto)return toast('Escribe o dicta el conteo.','mal');

  const cuerpo={sesion:S.sesion,contador:S.contadores[S.turno%S.contadores.length],
                texto};
  const d=await api('/api/conteo',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(cuerpo)});

  if(!d.ok){
    if(d.tipo==='sin_coincidencia'){
      $('#resConteo').innerHTML=`<div class="aviso mal">
        <b>No encontré «${esc(d.buscado)}» en el catálogo.</b>
        ${d.alternativas.length?'<br>¿Quisiste decir?<div class="fila" '+
          'style="margin-top:9px">'+d.alternativas.slice(0,4).map(a=>
          `<button class="b-sec" style="padding:7px 12px;min-height:auto;
            font-size:13px" onclick="usar('${esc(a.producto).replace(/'/g,"\\'")}')">
            ${esc(a.producto)} <span style="color:var(--texto-secundario)">
            ${a.stock_disponible} ${esc(a.unidad)}</span></button>`).join('')+
          '</div>':''}</div>`;
    }else{
      $('#resConteo').innerHTML=`<div class="aviso mal">
        <b>No entendí el dictado.</b><br>${(d.errores||[]).map(esc).join('<br>')}
        </div>`;
    }
    return;
  }

  if(d.bloqueado){pintarBloqueo(d.resultado,texto);return;}

  const r=d.resultado;
  const clase=r.estado==='OK'?'bien':r.estado==='ALERTA'?'':'mal';
  $('#resConteo').innerHTML=`<div class="aviso ${clase}">
    <b>${esc(r.producto)} · ${esc(r.estado.replace('_',' '))}</b><br>
    Sistema ${r.stock_disponible} ${esc(r.unidad_catalogo)} ·
    contado ${r.cantidad_normalizada} ·
    diferencia ${r.diferencia} (${r.error_pct} %)
    <div style="margin-top:7px;font-size:12px;color:var(--texto-secundario)">
    ${r.mensajes.map(esc).join('<br>')}</div></div>`;

  $('#dictado').value='';previa();
  S.turno++;$('#quien').textContent=S.contadores[S.turno%S.contadores.length];
  $('#dictado').focus();
  refrescar();
};

window.usar=nombre=>{
  const t=$('#dictado').value, p=t.split(',');
  $('#dictado').value=p.length>=3?[nombre,...p.slice(1)].join(','):nombre;
  previa();$('#dictado').focus();
};

function pintarBloqueo(r,texto){
  S.bloqueo={producto:r.producto,unidad:r.unidad_catalogo,texto};
  $('#resConteo').innerHTML=`
    <div class="bloqueo">
      <div class="tit">⛔ Conteo bloqueado · no se registró</div>
      <div class="msg">${esc(r.pregunta)}</div>
      <div class="campos">
        <div>
          <label for="cant">Cantidad en ${esc(r.unidad_catalogo)}</label>
          <input id="cant" type="number" step="any" class="mono"
                 placeholder="0" autofocus>
        </div>
        <button class="b-pri" onclick="confirmarBloqueo()">Confirmar</button>
        <button class="b-sec" onclick="$('#resConteo').innerHTML='';S.bloqueo=null">
          Cancelar</button>
      </div>
      <div style="margin-top:11px;font-size:12px;color:var(--texto-secundario)">
        ${r.mensajes.map(esc).join('<br>')}</div>
    </div>`;
  setTimeout(()=>$('#cant')?.focus(),60);
}

window.confirmarBloqueo=async()=>{
  const v=$('#cant').value;
  if(v===''||isNaN(v))return toast('Escribe la cantidad.','mal');
  const d=await api('/api/conteo',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sesion:S.sesion,
      contador:S.contadores[S.turno%S.contadores.length],
      texto:S.bloqueo.texto,producto:S.bloqueo.producto,
      forzar_unidad:S.bloqueo.unidad,cantidad:parseFloat(v)})});
  if(!d.ok)return toast(d.error||'No se pudo registrar.','mal');

  const r=d.resultado;
  $('#resConteo').innerHTML=`<div class="aviso bien">
    <b>Registrado en ${esc(r.unidad_catalogo)}.</b><br>
    ${esc(r.producto)}: ${r.cantidad_normalizada} ${esc(r.unidad_catalogo)} ·
    diferencia ${r.diferencia} (${r.error_pct} %)</div>`;
  $('#dictado').value='';previa();S.bloqueo=null;
  S.turno++;$('#quien').textContent=S.contadores[S.turno%S.contadores.length];
  refrescar();
};

/* Dictado por voz (si el navegador lo permite) */
const RC=window.SpeechRecognition||window.webkitSpeechRecognition;

function limpiarRuidoDictado(texto){
  // Quita muletillas e interjecciones que el reconocimiento suele
  // transcribir a partir de ruido de fondo o titubeos ("eh", "mmm"…),
  // sin tocar el resto de la frase dictada.
  return String(texto||'').trim()
    .replace(/\b(eh+|ah+|oh+|mm+|hm+|umm+|uh+|este|o sea)\b/gi, ' ')
    .replace(/\s+/g, ' ').trim();
}

if(RC){
  const rec=new RC();
  rec.lang='es-CO';
  rec.interimResults=false;
  rec.maxAlternatives=5;
  $('#btnVoz').onclick=()=>{
    if($('#btnVoz').classList.contains('escuchando')){try{rec.stop()}catch{}return}
    try{rec.start();$('#btnVoz').textContent='Escuchando';
      $('#btnVoz').classList.add('escuchando')}catch{}
  };
  rec.onresult=e=>{
    // Entre las alternativas que da el motor de voz, se descartan las
    // que quedan vacías tras filtrar ruido y se usa la de mayor confianza.
    const candidatos=[...e.results[0]]
      .map(a=>({texto:limpiarRuidoDictado(a.transcript),confianza:a.confidence||0}))
      .filter(a=>a.texto.length>1)
      .sort((a,b)=>b.confianza-a.confianza);
    if(!candidatos.length){
      toast('Solo se detectó ruido de fondo. Acércate al micrófono e intenta de nuevo.','mal');
      return;
    }
    $('#dictado').value=candidatos[0].texto;
    previa();
  };
  rec.onend=()=>{$('#btnVoz').textContent='Dictar';$('#btnVoz').classList.remove('escuchando')};
  rec.onerror=e=>{$('#btnVoz').textContent='Dictar';$('#btnVoz').classList.remove('escuchando');
    toast(e.error==='no-speech'
      ?'No se detectó voz, solo ruido de fondo. Intenta de nuevo.'
      :'No pude escuchar. Escribe el conteo.','mal')};
}else{
  $('#btnVoz').disabled=true;$('#btnVoz').title='Tu navegador no admite dictado por voz';
}

/* ── Estado de la sesión ── */
async function refrescar(){
  if(!S.sesion)return;
  const d=await api('/api/sesion/'+S.sesion);
  if(!d.ok)return;
  pintarRegistros(d.registros);
  pintarLog(d.bitacora);
  const n=d.resumen.pendientes_auditoria;
  const b=$('#badge');b.textContent=n;b.classList.toggle('oculto',!n);
  S._registros=d.registros;S._resumen=d.resumen;
}

function pintarRegistros(regs){
  if(!regs.length){$('#tablaRegistros').innerHTML=
    '<div style="color:var(--texto-secundario);font-size:13px">Aún no hay conteos.</div>';return;}
  const etq={APROBADO:'et-ok',PENDIENTE_AUDITORIA:'et-warn',
    PENDIENTE_CONTEO:'et-info',RECONTEO:'et-mal',RECHAZADO:'et-mal'};
  $('#tablaRegistros').innerHTML=`<div class="tabla-wrap">
    <table><thead><tr><th>Producto</th><th>Unidad</th><th>Sistema</th>
      <th>Conteos</th><th>Consenso</th><th>Dif.</th><th>Error</th>
      <th>Estado</th></tr></thead><tbody>
    ${regs.map(r=>`<tr>
      <td>${esc(r.producto)}</td>
      <td><span class="et et-info">${esc(r.unidad)}</span></td>
      <td class="num">${r.stock_disponible}</td>
      <td class="mono" style="font-size:11px">${Object.entries(r.conteos)
        .map(([c,v])=>`${esc(c.split(' ')[0])}: ${v.cantidad}`).join('<br>')
        ||'<span style="color:var(--texto-secundario)">por contar</span>'}
        ${r.conteos_previos?`<div style="color:var(--texto-secundario);margin-top:4px;
          text-decoration:line-through">${Object.entries(r.conteos_previos)
          .map(([c,v])=>`${esc(c.split(' ')[0])}: ${v.cantidad}`).join('<br>')}
          </div>`:''}</td>
      <td class="num">${r.consenso??'—'}</td>
      <td class="num" style="color:${r.diferencia<0?'var(--rojo)':
        r.diferencia>0?'#c98300':'var(--texto-secundario)'}">${r.diferencia??'—'}</td>
      <td class="num">${r.error_pct!=null?r.error_pct+' %':'—'}</td>
      <td><span class="et ${etq[r.estado]||'et-gris'}">
        ${esc(r.estado.replace(/_/g,' '))}</span>
        ${r.motivo?`<div style="font-size:10px;color:var(--texto-secundario);margin-top:4px;
          max-width:190px">${esc(r.motivo)}</div>`:''}</td>
      </tr>`).join('')}</tbody></table></div>`;
}

function pintarLog(bit){
  $('#log').innerHTML=bit.length?bit.slice().reverse().map(b=>
    `<div><span class="t">${esc(b.ts.slice(11,19))}</span>
     <span class="a">${esc(b.actor)}</span>
     <span>${esc(b.accion)} · ${esc(Object.values(b.detalle).join(' · '))}</span>
     </div>`).join(''):'<div style="color:var(--texto-secundario)">Sin movimientos.</div>';
}

/* ══════════ 4 · AUDITORÍA ══════════ */
async function pintarAuditoria(){
  if(!S.sesion)return;
  await refrescar();
  const regs=S._registros||[],r=S._resumen||{};

  $('#resumenAud').innerHTML=`<div class="mets">
    ${met(r.total_registros||0,'Registros')}
    ${met(r.aprobados||0,'Aprobados','bien')}
    ${met(r.pendientes_auditoria||0,'Por dictaminar',
          r.pendientes_auditoria?'alerta':'')}
    ${met(r.reconteo||0,'En reconteo',r.reconteo?'mal':'')}
    ${met(r.anomalias||0,'Anomalías graves',r.anomalias?'mal':'')}
  </div>`;

  const pend=regs.filter(x=>x.estado==='PENDIENTE_AUDITORIA');
  const otros=regs.filter(x=>x.estado!=='PENDIENTE_AUDITORIA');

  if(!pend.length&&!otros.length){
    $('#listaAud').innerHTML=`<div class="vacio-msg"><div class="ico">◷</div>
      <b>Todavía no hay nada que auditar</b>
      Registra conteos en la pestaña anterior.</div>`;return;}

  const sev=s=>s==='ALTA'||s==='CRITICA'?'grave':s==='MEDIA'?'media':'leve';
  const etSev=s=>s==='ALTA'||s==='CRITICA'?'et-mal':
    s==='MEDIA'||s==='LEVE'?'et-warn':'et-ok';

  $('#listaAud').innerHTML=
    (pend.length?`<h3 style="font-size:12px;text-transform:uppercase;
      letter-spacing:.12em;color:var(--texto-tenue);margin:20px 0 11px">
      Pendientes de dictamen (${pend.length})</h3>`:'')+
    pend.map((r,i)=>`
    <div class="aud ${sev(r.severidad)}">
      <div class="cab">
        <div><div class="nom">${esc(r.producto)}</div>
          <div style="font-size:12px;color:var(--texto-secundario)">${esc(r.bodega||'')}</div>
        </div>
        <span class="et ${etSev(r.severidad)}">${esc(r.severidad||'sin desviación')}</span>
      </div>
      <div class="cifras">
        <div><b>Sistema</b>${r.stock_disponible} ${esc(r.unidad)}</div>
        ${Object.entries(r.conteos).map(([c,v])=>
          `<div><b>${esc(c)}</b>${v.cantidad}</div>`).join('')}
        <div><b>Consenso</b>${r.consenso}</div>
        <div><b>Diferencia</b><span style="color:${r.diferencia<0?
          'var(--rojo)':'#c98300'}">${r.diferencia}</span></div>
        <div><b>Error</b>${r.error_pct} %</div>
        ${r.dispersion_pct!=null?
          `<div><b>Dispersión</b>${r.dispersion_pct} %</div>`:''}
      </div>
      <div class="grid g2" style="margin-bottom:12px">
        <div><label>Cantidad final (opcional)</label>
          <input id="ac${i}" type="number" step="any" class="mono"
                 placeholder="${r.consenso}"></div>
        <div><label>Comentario</label>
          <input id="am${i}" placeholder="Observación del auditor"></div>
      </div>
      <div class="fila">
        <button class="b-ok" onclick="dictaminar(${i},'APROBAR','${esc(r.producto)
          .replace(/'/g,"\\'")}','${esc(r.unidad)}')">Aprobar</button>
        <button class="b-sec" onclick="dictaminar(${i},'RECONTEO','${esc(r.producto)
          .replace(/'/g,"\\'")}','${esc(r.unidad)}')">Reconteo</button>
        <button class="b-no" onclick="dictaminar(${i},'RECHAZAR','${esc(r.producto)
          .replace(/'/g,"\\'")}','${esc(r.unidad)}')">Rechazar</button>
      </div>
    </div>`).join('')+
    (otros.length?`<h3 style="font-size:12px;text-transform:uppercase;
      letter-spacing:.12em;color:var(--texto-tenue);margin:26px 0 11px">
      Otros registros (${otros.length})</h3>`+otros.map(r=>`
      <div class="aud"><div class="cab">
        <div><div class="nom" style="font-size:15px">${esc(r.producto)}</div>
          ${r.motivo?`<div style="font-size:12px;color:#c98300;margin-top:4px">
            ${esc(r.motivo)}</div>`:''}
          ${r.dictamen?`<div style="font-size:12px;color:var(--texto-secundario);margin-top:4px">
            ${esc(r.dictamen.auditor)} · ${esc(r.dictamen.comentario||'sin comentario')}
            </div>`:''}
        </div>
        <span class="et ${r.estado==='APROBADO'?'et-ok':
          r.estado==='RECONTEO'?'et-mal':'et-gris'}">
          ${esc(r.estado.replace(/_/g,' '))}</span>
      </div></div>`).join(''):'');
}

window.dictaminar=async(i,decision,producto,unidad)=>{
  const d=await api('/api/auditar',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sesion:S.sesion,auditor:S.auditor,producto,unidad,
      decision,cantidad_auditor:$('#ac'+i)?.value||null,
      comentario:$('#am'+i)?.value||''})});
  if(!d.ok)return toast(d.error,'mal');
  if(decision==='RECONTEO'){
    toast('Reconteo ordenado · vuelve a contar '+producto,'');
    await refrescar();ir('conteo');
    $('#dictado').value=producto+', '+unidad+', ';
    $('#dictado').focus();return;
  }
  toast(decision==='APROBAR'?'Aprobado':'Rechazado','bien');
  pintarAuditoria();
};

$('#btnCerrar').onclick=async()=>{
  const d=await api('/api/cerrar',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sesion:S.sesion,auditor:S.auditor})});
  if(!d.ok){
    $('#resumenAud').insertAdjacentHTML('afterbegin',
      `<div class="aviso mal"><b>La sesión sigue abierta.</b><br>${esc(d.error)}
       ${(d.abiertos||[]).map(a=>`<div style="margin-top:5px;font-size:12px">
       · ${esc(a.producto)} — ${esc(a.estado.replace(/_/g,' '))}
       ${esc(a.motivo||'')}</div>`).join('')}</div>`);
    return toast('Faltan registros por aprobar.','mal');
  }
  $('#resumenAud').insertAdjacentHTML('afterbegin',
    `<div class="aviso bien"><b>Sesión ${esc(d.resumen.sesion)} cerrada.</b><br>
     ${d.resumen.aprobados} de ${d.resumen.total_registros} registros aprobados ·
     ${d.resumen.anomalias} anomalías graves.</div>`);
  toast('Sesión cerrada','bien');
};

$('#btnDescargar').onclick=()=>{
  if(!S.sesion)return toast('No hay sesión abierta.','mal');
  location.href=`/api/sesion/${S.sesion}/exportar`;
};

window.ir=ir;

/* ══════════ 5 · PERFIL ══════════ */

let perfilOriginal = null;


/* Traduce el código interno del rol a un texto visible */
function nombreRol(rol){
  const roles = {
    contador: 'Contador',
    auditor: 'Auditor',
    encargado: 'Encargado de bodega',
    administrador: 'Administrador'
  };

  return roles[rol] || 'Sin rol asignado';
}


/* Llena el selector con las bodegas del catálogo cargado */
function cargarBodegasPerfil(bodegaSeleccionada = ''){
  const select = $('#profileWarehouse');

  if(!select) return;

  const bodegas = [
    ...new Set(
      (S.bodegasCarga || [])
        .map(b => String(b || '').trim())
        .filter(Boolean)
    )
  ];

  select.innerHTML =
    '<option value="">Selecciona una bodega</option>' +
    bodegas.map(bodega => `
      <option
        value="${esc(bodega)}"
        ${bodega === bodegaSeleccionada ? 'selected' : ''}
      >
        ${esc(bodega)}
      </option>
    `).join('');

  /*
   * Si el perfil tiene una bodega que no aparece en el catálogo,
   * la conservamos para no borrar accidentalmente su valor.
   */
  if(
    bodegaSeleccionada &&
    !bodegas.includes(bodegaSeleccionada)
  ){
    select.insertAdjacentHTML(
      'beforeend',
      `<option value="${esc(bodegaSeleccionada)}" selected>
        ${esc(bodegaSeleccionada)}
      </option>`
    );
  }
}


/* Pinta la información en formulario, resumen y encabezado */
function pintarPerfil(perfil = {}){
  const nombre = perfil.nombre || '';
  const rol = perfil.rol || '';
  const bodega = perfil.bodega || '';

  $('#profileName').value = nombre;
  $('#profileEmail').value = perfil.email || '';
  $('#profilePhone').value = perfil.telefono || '';
  $('#profileRole').value = rol;
  $('#profileDocument').value = perfil.documento || '';

  cargarBodegasPerfil(bodega);

  $('#profileDisplayName').textContent =
    nombre || 'Usuario de Inventario 360';

  $('#profileDisplayRole').textContent = nombreRol(rol);

  $('#profileDisplayWarehouse').textContent =
    bodega || 'Sin asignar';

  $('#profileStatus').textContent =
    perfil.estado || 'Cuenta activa';

  $('#profileLastAccess').textContent =
    perfil.ultimo_acceso || 'Hoy';

  /*
   * También actualiza el encabezado general de la aplicación.
   */
  $('#avatar').textContent = iniciales(nombre);
  $('#perfilNombre').textContent =
    nombre || 'Inventario 360';

  $('#perfilSub').textContent = [
    nombreRol(rol),
    bodega
  ].filter(Boolean).join(' · ');

  perfilOriginal = {
    nombre: perfil.nombre || '',
    email: perfil.email || '',
    telefono: perfil.telefono || '',
    rol: perfil.rol || '',
    bodega: perfil.bodega || '',
    documento: perfil.documento || '',
    estado: perfil.estado || 'Cuenta activa',
    ultimo_acceso: perfil.ultimo_acceso || 'Hoy'
  };
}


/* Consulta el perfil guardado en server.py */
async function cargarPerfil(){
  try{
    const d = await api('/api/perfil');

    if(!d.ok){
      throw new Error(d.error || 'No se pudo consultar el perfil.');
    }

    pintarPerfil(d.perfil || {});
  }catch(error){
    toast(error.message, 'mal');
  }
}


/* Intercepta el formulario para evitar que recargue la página */
$('#profileForm').onsubmit = async event => {
  event.preventDefault();

  const boton = $('#profileSaveButton');
  const textoOriginal = boton.textContent;

  const perfil = {
    nombre: $('#profileName').value.trim(),
    email: $('#profileEmail').value.trim(),
    telefono: $('#profilePhone').value.trim(),
    rol: $('#profileRole').value,
    bodega: $('#profileWarehouse').value,
    documento: $('#profileDocument').value.trim()
  };

  if(!perfil.nombre){
    return toast('El nombre es obligatorio.', 'mal');
  }

  if(!perfil.email){
    return toast('El correo electrónico es obligatorio.', 'mal');
  }

  boton.disabled = true;
  boton.textContent = 'Guardando…';

  try{
    const d = await api('/api/perfil', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(perfil)
    });

    if(!d.ok){
      throw new Error(d.error || 'No se pudo guardar el perfil.');
    }

    pintarPerfil(d.perfil);

    const mensaje = $('#profileMessage');
    mensaje.textContent =
      d.mensaje || 'Perfil actualizado correctamente.';
    mensaje.className = 'aviso bien';

    toast(
      d.mensaje || 'Perfil actualizado correctamente.',
      'bien'
    );

  }catch(error){
    const mensaje = $('#profileMessage');
    mensaje.textContent = error.message;
    mensaje.className = 'aviso mal';

    toast(error.message, 'mal');

  }finally{
    boton.disabled = false;
    boton.textContent = textoOriginal;
  }
};


/* Descartar cambios y volver al último perfil guardado */
$('#profileResetButton').onclick = () => {
  if(!perfilOriginal) return;

  pintarPerfil(perfilOriginal);

  const mensaje = $('#profileMessage');
  mensaje.textContent = '';
  mensaje.className = 'aviso oculto';

  toast('Cambios descartados.');
};


/* Vista previa local de la fotografía */
$('#profileImageInput').onchange = event => {
  const archivo = event.target.files?.[0];

  if(!archivo) return;

  if(!archivo.type.startsWith('image/')){
    event.target.value = '';
    return toast('Selecciona una imagen válida.', 'mal');
  }

  if(archivo.size > 3 * 1024 * 1024){
    event.target.value = '';
    return toast('La imagen no puede superar 3 MB.', 'mal');
  }

  const lector = new FileReader();

  lector.onload = () => {
    $('#profileAvatar').src = lector.result;
    toast('Vista previa actualizada.', 'bien');
  };

  lector.readAsDataURL(archivo);
};


/* Por ahora solo simula el cierre de sesión */
$('#logoutButton').onclick = () => {
  abrirBienvenida();
  ir('datos');
  toast('Sesión finalizada.');
};


/* Carga inicial del perfil */
cargarPerfil();

/* ── Catálogo de referencia precargado por el servidor ── */
(async function cargarCatalogoPrecargado(){
  try{
    const d=await api('/api/catalogo');
    if(!d.ok)return;
    d._sinToast=true;
    pintarCatalogo(d);
    toast(`Catálogo de referencia cargado: ${d.reporte.filas_final} productos`,'bien');
  }catch(e){/* sin catálogo precargado: se sigue pidiendo carga manual */}
})();
