# Productos tendencia para importar

## Resumen ejecutivo

Es técnicamente factible convertir MiFly Market Intelligence en un sistema que detecte productos con potencial para importar desde China y vender en Chile, con extensión posterior a Latinoamérica. Sin embargo, ninguna fuente aislada demuestra simultáneamente interés, ventas, tamaño de mercado, margen y viabilidad regulatoria. La solución recomendada es triangular cuatro tipos de señales:

1. **Interés emergente:** búsquedas y actividad social.
2. **Demanda comercial:** rankings de marketplaces, precios, reseñas y rotación observable.
3. **Profundidad del mercado:** importaciones mensuales por código HS, cantidades y valores unitarios.
4. **Factibilidad de abastecimiento:** precio EXW/FOB, MOQ, flete, calidad, certificaciones y costo puesto en Chile.

El MVP debe comenzar con fuentes oficiales y automatizables: Mercado Libre Trends, UN Comtrade y datos aduaneros; complementarlas con Google Trends y TikTok Creative Center como señales de descubrimiento; y mantener la página pública “Más vendidos” como fuente secundaria, porque Mercado Libre bloquea actualmente el acceso automatizado desde servidores.

## Qué pregunta debe responder el producto

La pregunta útil no es solamente “¿qué está en tendencia?”, sino:

> ¿Qué producto muestra demanda creciente y persistente en Chile o Latinoamérica, tiene competencia y precio compatibles con un margen atractivo, puede abastecerse de forma confiable desde China y puede importarse y comercializarse legalmente con un riesgo razonable?

Una tendencia puede ser viral pero no comprable, tener un mercado demasiado pequeño, estar saturada, requerir certificaciones caras o dejar margen negativo después de flete, impuestos, devoluciones y comisiones.

## Evaluación de alternativas

| Fuente | Señal | Chile | Latinoamérica | Automatización | Utilidad | Limitación principal | Prioridad |
|---|---|---:|---:|---:|---:|---|---:|
| Mercado Libre Trends API | Búsquedas semanales | Sí | AR, BR, CL, CO, MX, PE, UY | Alta, API oficial | Alta | Devuelve keywords y enlaces, no ventas ni volumen absoluto | 1 |
| Mercado Libre “Más vendidos” web | Ranking, precio, rating y rangos vendidos | Sí | Depende del sitio | Baja | Muy alta | Las páginas de categoría respondieron 403 a acceso automatizado; HTML cambiante y riesgo de términos de uso | 2, manual/contingente |
| Mercado Libre Highlights API | Top 20 oficial por categoría | Sí | Sí | Alta en teoría | Muy alta | La aplicación recibe `PA_UNAUTHORIZED_RESULT_FROM_POLICIES` pese a tener el scope correcto | En espera de MELI |
| Google Trends | Interés relativo, crecimiento, geografía y estacionalidad | Sí | Sí | Media hoy; alta si aceptan acceso Alpha | Muy alta | Índice relativo, no unidades; API oficial todavía en acceso Alpha | 1 |
| TikTok Creative Center | Hashtags, videos, anuncios y productos creativos | Chile limitado/no visible en el selector actual; útil como señal regional | AR, BR, CO y MX visibles, entre otros | Media-baja | Alta para descubrimiento | Viralidad no equivale a compras; cobertura geográfica desigual | 2 |
| Amazon Best Sellers / Movers & Shakers | Ventas relativas y aceleración en 24 h | No como mercado chileno | Útil en MX/BR y como señal anticipada global | Baja-media | Alta para descubrir productos | Sesgo hacia mercados Amazon; no prueba demanda chilena | 2 |
| Alibaba Keyword Trends | Interés de compradores B2B, países, 7/30 días e histórico | Indirecto | Sí | Media, normalmente asociada a cuenta/plataforma | Alta para abastecimiento | Mide demanda de compradores mayoristas, no consumidor final | 2 |
| UN Comtrade | Importaciones mensuales/anuales por HS, socio, valor y peso | Sí | ~200 países | Alta, API oficial | Muy alta para validación | Rezago y granularidad HS; no identifica necesariamente modelos/SKUs | 1 |
| ITC Trade Map / Market Access Map | Demanda importadora, crecimiento, unit values, aranceles | Sí | Sí | Media | Muy alta | Registro/licencia y datos agregados | 1-2 |
| Aduanas de Chile | Importaciones oficiales y arancel | Sí | No | Media, según dataset | Muy alta | Clasificación aduanera y rezago | 1 |
| NIQ/GfK | Sell-out, precio, canal y SKU en tecnología y durables | Sí según cobertura contratada | Sí | Alta mediante servicio contratado | Muy alta | Coste comercial, acceso no abierto | 3, cuando el negocio lo justifique |
| Statista | Tamaño y pronóstico de mercados | Sí en muchos mercados | Sí | Baja-media según licencia | Media-alta para contexto | Modelado, actualización semestral y acceso detallado de pago | 3 |
| Similarweb | Tráfico, canales y competidores digitales | Sí | Sí | Media/pago | Media | Tráfico no equivale a ventas por producto | 3 |
| Meta Ad Library | Creatividades y persistencia publicitaria | UI para anuncios comerciales | Sí | Baja para LatAm | Media | La API de todos los anuncios se concentra en UE/Reino Unido; en LatAm el análisis comercial es principalmente manual | 3 |
| Pew / Our World in Data / CEPAL | Contexto demográfico y macro | Sí | Sí | Alta | Baja para escoger SKU | No detecta demanda de productos concreta | Contexto בלבד |

## Hallazgos comprobados

### Mercado Libre Trends

Mercado Libre documenta oficialmente `/trends/{SITE_ID}` y `/trends/{SITE_ID}/{CATEGORY_ID}`. Entrega 50 resultados semanales:

- posiciones 1–10: búsquedas con mayor crecimiento;
- posiciones 11–30: búsquedas más deseadas;
- posiciones 31–50: tendencias populares frente a dos semanas atrás.

Está disponible para Argentina, Brasil, Chile, México, Colombia, Uruguay y Perú. Cada registro contiene `keyword` y `url`, no volumen de búsqueda ni ventas. Es una excelente fuente de descubrimiento, pero debe enriquecerse y validarse con otras señales. [Documentación oficial](https://developers.mercadolibre.cl/es_cl/tendencias)

La prueba real del 28 de octubre de 2026 fue exitosa con token de aplicación: `/trends/MLC` devolvió 50 resultados y `/trends/MLC/MLC82067` devolvió 45 tendencias de Tablets. Entre las señales accionables aparecen “tablet con lápiz”, “tablet con chip 4G” y “tablet con teclado”. El servicio quedó disponible en TopSellers mediante `/api/v1/trends` y `/api/v1/trends/{category_id}`.

### Mercado Libre “Más vendidos” público

La página oficial muestra ranking, título, precio actual, rating, rangos aproximados de unidades vendidas y enlaces de producto. Esto permitiría snapshots diarios útiles. No obstante, las pruebas de acceso automatizado a categorías específicas devolvieron HTTP 403, por lo que no debe ser la base operacional sin autorización expresa o un mecanismo permitido por Mercado Libre. La página sigue siendo valiosa para revisión manual y como respaldo del ranking. [Página oficial](https://www.mercadolibre.cl/mas-vendidos/)

### Google Trends

Google permite comparar interés por país y subregión y exportar CSV desde la interfaz. Su API oficial ofrece cinco años, intervalos regulares y escalas comparables, pero en octubre de 2026 continúa como Alpha con admisión previa. Conviene solicitar acceso y, mientras tanto, usar consultas curadas y exportaciones conformes a sus términos. [API Alpha](https://developers.google.com/search/apis/trends), [exportación oficial](https://support.google.com/trends/answer/4365538)

### TikTok Creative Center

Es público y gratuito; ofrece tendencias de hashtags, videos, Top Ads y señales creativas. El selector observado incluye Argentina, Brasil, Colombia y México, pero no Chile. Es útil para descubrir tendencias que pueden llegar a Chile, nunca como evidencia única de ventas. [Creative Center](https://ads.tiktok.com/resources/help/article/creative-center), [uso de Trends](https://ads.tiktok.com/resources/help/article/how-to-use-trends)

### Comercio exterior

UN Comtrade cubre aproximadamente 200 países y más de 99% del comercio mundial, con datos mensuales/anuales, socio, código HS, valor, peso y API. El plan gratuito contempla hasta 500 llamadas diarias y 100.000 registros por consulta, suficiente para un MVP. [Plataforma](https://comtradeplus.un.org/), [límites](https://comtradeplus.un.org/Subscriptions)

Trade Map añade crecimiento en valor/cantidad, unit values, estacionalidad y socios; Market Access Map ayuda con aranceles y barreras. Estos datos sirven para confirmar que una señal digital corresponde a un mercado material y para estimar precios de importación de referencia. [ITC Market Analysis Tools](https://marketanalysis.intracen.org/en)

### Abastecimiento en China

Alibaba Keyword Trends ofrece tendencias de 7/30 días, histórico de dos años, distribución geográfica y búsquedas relacionadas. Es valioso para evaluar disponibilidad y presión B2B, pero no representa ventas minoristas locales. Para proveedores deben utilizarse muestras, auditoría documental, reportes verificables y Trade Assurance; el distintivo de proveedor no reemplaza control de calidad. [Keyword Trends](https://seller.alibaba.com/keywords-trends?locale=en), [proceso de sourcing](https://activity.alibaba.com/page/HowItWorks/Page.html)

## Riesgo regulatorio en Chile

La regulación debe operar como un filtro excluyente antes de recomendar una compra:

- Productos eléctricos regulados necesitan certificación y Sello SEC antes de comercializarse. [SEC](https://www.sec.cl/sistema-de-certificacion-de-productos-sec/)
- Equipos con Wi-Fi, Bluetooth, radio, controles inalámbricos o IoT pueden requerir certificación SUBTEL; la respuesta formal puede tardar hasta 20 días hábiles. [SUBTEL](https://www.subtel.gob.cl/certificacion-de-equipos-de-telecomunicaciones/)
- Cosméticos requieren autorización sanitaria; su importación se coordina con ISP y Aduanas. [ISP Cosméticos](https://www.ispch.cl/anamed_/cosmeticos), [Importación ISP](https://www.ispch.cl/anamed/importacion-y-exportacion/)
- Etiquetado en español, sistema métrico y país de origen son requisitos comunes; existen reglas adicionales según producto. [Requisitos de etiquetado](https://www.trade.gov/knowledge-product/chile-labeling-marking-requirements)
- Chile aplica IVA de 19% sobre valor importado más derecho aduanero; el arancel efectivo depende de clasificación y origen preferencial. [Canales e importación](https://www.trade.gov/country-commercial-guides/chile-distribution-sales-channels)

Para un primer MVP conviene priorizar productos no ingeribles, no cosméticos, sin baterías de alta energía, sin transmisión radioeléctrica y sin certificación SEC obligatoria. Esto reduce tiempo, capital inmovilizado y riesgo de rechazo.

## Arquitectura recomendada

### Capa 1: descubrimiento

- Mercado Libre Trends semanal por país y categoría.
- Google Trends por país/subregión, crecimiento 7/30/90/365 días y estacionalidad.
- TikTok Creative Center para señales regionales.
- Amazon Movers & Shakers y Hot New Releases como radar global, no como validación chilena.

### Capa 2: validación comercial

- Rankings oficiales de marketplaces cuando estén disponibles.
- Precio, rating, reseñas, antigüedad y cantidad de competidores.
- Persistencia: una oportunidad debe aparecer en varias semanas o fuentes, no ser un pico único.
- Revisión manual de Mercado Libre “Más vendidos” mientras Highlights esté restringido.

### Capa 3: tamaño y saturación

- Importaciones mensuales de Chile y mercados comparables por HS6.
- Crecimiento interanual en valor y cantidad.
- Valor unitario de importación y concentración de proveedores.
- Número de ofertas y dispersión de precios locales.

### Capa 4: abastecimiento y economía unitaria

- Proveedores verificados, MOQ, muestra, EXW/FOB, plazo y defect rate acordado.
- Flete, seguro, arancel, IVA, agente, certificación, etiquetado y almacenaje.
- Comisión del canal, costo de última milla, devoluciones, garantía y marketing.
- Margen de contribución y capital requerido, no solo diferencia entre precio chino y precio chileno.

## Modelo de decisión propuesto

No debe publicarse un “producto ganador” con una sola métrica. Cada candidato puede recibir indicadores separados y auditables:

- **Momentum de demanda:** crecimiento y persistencia en búsquedas.
- **Prueba comercial:** presencia y estabilidad en rankings/ventas observables.
- **Profundidad de mercado:** valor y crecimiento de importaciones.
- **Competencia:** vendedores, marcas, precios y reseñas.
- **Economía unitaria:** margen de contribución estimado y sensibilidad al flete/tipo de cambio.
- **Riesgo regulatorio:** bajo, medio, alto o bloqueante.
- **Riesgo de abastecimiento:** MOQ, concentración, certificaciones y calidad.

La recomendación final debe mostrar por qué un producto avanza o se descarta y mantener los datos originales como evidencia.

## Plan de implementación

### Fase 1 — Radar oficial y gratuito (2–3 semanas)

1. Generalizar Mercado Libre Trends para los siete países admitidos.
2. Guardar snapshots semanales por país, categoría, segmento y posición.
3. Crear taxonomía de keywords a familias de productos.
4. Integrar UN Comtrade para Chile y comparables mediante HS6.
5. Añadir una lista regulatoria inicial SEC/SUBTEL/ISP.
6. Dashboard con tendencias persistentes, nuevas y descendentes; sin afirmar ventas donde solo existe búsqueda.

### Fase 2 — Validación de producto (3–5 semanas)

1. Solicitar acceso a Google Trends API Alpha; mientras tanto, permitir CSV oficiales.
2. Incorporar señales curadas de TikTok y Amazon.
3. Construir flujo de candidatos: keyword → producto normalizado → código HS → competidores → proveedores.
4. Añadir calculadora de costo puesto en Chile y margen de contribución.
5. Mantener “Más vendidos” como captura manual o fuente autorizada hasta resolver Highlights.

### Fase 3 — Inteligencia comercial (posterior)

1. Evaluar licencia NIQ/GfK para tecnología y durables cuando el ahorro esperado supere su costo.
2. Automatizar RFQ, muestras y comparativas de proveedores sin realizar compras automáticas.
3. Extender modelos país por país, porque precio, regulación, canal y estacionalidad no son intercambiables.

## Recomendación final

Conviene ampliar el alcance del proyecto a **Radar de Oportunidades de Importación**, manteniendo Mercado Libre como una señal relevante pero no como el único pilar. El MVP más defendible combina Mercado Libre Trends + Google Trends + UN Comtrade/Aduanas + validación de abastecimiento Alibaba. TikTok y Amazon deben actuar como radares de anticipación. NIQ/GfK puede convertirse en la capa premium para electrónica y electrodomésticos cuando exista presupuesto.

No se recomienda basar el negocio en scraping continuo de Mercado Libre “Más vendidos”: hoy es técnicamente frágil, ya muestra bloqueos 403 y crea dependencia de una interfaz no contractual. Sí se recomienda usar la página para revisión manual, documentar snapshots permitidos y continuar gestionando con Mercado Libre el acceso oficial a Highlights.
