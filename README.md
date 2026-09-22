# 🎬 Novel → Animated Series Engine

Sistema en Python para convertir una novela en una serie animada generada con IA.

El motor analiza el texto, detecta contexto narrativo y construye automáticamente escenas cinematográficas con:

- personajes consistentes
- escenarios y props
- dirección de cámara
- diálogo y narración
- composición de escenas
- generación de clips de vídeo

---

> **Estado:** proyecto personal en desarrollo (*work in progress*).  
> El repositorio refleja una arquitectura funcional y distintas líneas de experimentación; algunas capacidades siguen en evolución.

# 🚀 ¿Qué hace este proyecto?

Convierte un archivo `libro.txt` en una serie estructurada:

novela → escenas → beats → clips → episodio final

Incluye:

- 🎭 detección automática de personajes
- 🧠 análisis narrativo
- 🎥 planificación cinematográfica
- 🎬 generación de vídeo con IA
- 🧩 sistema modular configurable por JSON

---

# 🧱 Estructura del proyecto

project/
├── assets/
│   ├── characters/
│   ├── locations/
│   └── props/
│
├── config/
│   ├── assets_manifest.json
│   ├── character_aliases.json
│   ├── metadata_schema.json
│   ├── metadata_catalogs.json
│   ├── directing_rules.json
│   ├── shot_rules.json
│   ├── style_rules.json
│   └── transition_rules.json
│
├── src/
│   ├── detector_contexto.py
│   ├── director_cinematografico.py
│   ├── scene_blocking_engine.py
│   ├── scene_storyboard.py
│   ├── editor_montaje.py
│   └── series_studio.py
│
├── libro.txt
├── output/
├── temp/
└── requirements.txt

---

# ⚙️ Crear entorno e Instalación

- Ubuntu / Linux 
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Windows
```bash
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

# ▶️ Ejecución

## Interfaz principal
```bash
# streamlit run studio.py
streamlit run src/studio_fixed.py
```

---

# 🖥️ Uso de la interfaz

## 1. 📁 Selección del proyecto
En la barra lateral:
- introduce la ruta del proyecto
- la app cargará config, libro.txt y assets

## 2. 🧠 Inspector del libro
Permite:
- ver episodios, bloques, escenas y beats
- detectar personajes, props y localizaciones

## 3. 🎬 Storyboard
Muestra:
- plano (shot)
- encuadre (framing)
- cámara (camera)
- foco (focus)
- transición

## 4. 🎛️ Overrides manuales
Editar beats en:
config/beat_overrides.json

## 5. ⚙️ Edición de JSON
Permite modificar:
- directing_rules.json
- style_rules.json
- transition_rules.json

## 6. ▶️ Render
Genera:
- imagen
- audio
- vídeo
Output en:
output/

---

# 🧠 Pipeline

libro.txt
↓
parser
↓
detector_contexto
↓
shot_planner
↓
director_cinematografico
↓
scene_blocking_engine
↓
editor_montaje
↓
generación vídeo

---

# 🎯 Objetivo

Convertir cualquier novela en serie animada con dirección cinematográfica automática.

# 🚀 Necesitas registrarte en:
1. **Fal.ai** → para vídeo IA y lip sync
2. **ElevenLabs** → para voces
3. Opcional: **Google AI Studio / Gemini** si luego quieres automatizar desglose o prompts, pero este paquete no lo necesita.

# 🔧 Necesitas instalar:
1. FFmpeg (OBLIGATORIO)

MoviePy lo necesita:

- Ubuntu
```bash
sudo apt install ffmpeg
```

- Mac
```bash
brew install ffmpeg
```

- Windows
```bash
Descargar binario y añadir a PATH
```

### Assets
Los recursos visuales y sonoros incluidos en este repositorio son materiales
generados mediante herramientas de IA generativa para el desarrollo y prueba
del proyecto.

