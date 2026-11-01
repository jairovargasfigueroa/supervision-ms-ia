# 301 · Detección de casco y chaleco · tareas de `services/ia` (entrenamiento)

Requisitos y diseño: `docs/specs/301-deteccion-epp/` de `supervision-docs` (carpeta raíz del proyecto). El servicio de IA ya existe (401 a 504); esta spec solo agrega su parte de entrenamiento (`entrenamiento/epp/`), en un entorno aparte que no cambia las dependencias del servicio. Los archivos del modelo los usa la app de obra (`mobile/app-obra/assets/modelos/`).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Aplicar «Cambios a contratos y datos» que toquen este proyecto y documentar `entrenamiento/epp/` en `docs/estructura.md` y `CLAUDE.md`; `.gitignore` ignora `entrenamiento/**/datos/` y `entrenamiento/**/salida/saved_model/`
- [x] Elegir el dataset (Construction Site Safety, SH17 u otro público de EPP con personas, casco y chaleco): revisar licencia y clases, y documentar la fuente, la cita y la descarga en `entrenamiento/epp/README.md`
- [x] `requirements-entrenamiento.txt` (Python 3.12, `tensorflow` 2.21, `keras` 3, `numpy`, `pandas`, `scikit-learn`, `matplotlib`, `pillow`) y comprobación del entorno local
- [x] `preparar_recortes.py`: recortes de personas, etiquetas `casco`/`chaleco` con pesos 0 para lo indeterminado, división 70/15/15 por imagen; pruebas de las reglas de etiquetado con cajas de ejemplo
- [x] Notebook `entrenar_clasificador_epp.ipynb`: MobileNetV3-Small en dos fases, aumentos, evaluación con las métricas mínimas, elección de umbrales y matriz de confusión
- [x] Exportar a TFLite float16 y comprobar la equivalencia con Keras (50 recortes, diferencia ≤ 0,02); guardar `clasificador_epp.tflite`, `epp.json` y `metricas.json` en `salida/`
- [x] Copiar los modelos a `mobile/app-obra/assets/modelos/` (junto con `efficientdet_lite0.tflite` int8) y anotar en el README la versión entregada
