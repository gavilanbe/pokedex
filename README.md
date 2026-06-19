# 🔴 Pokédex Nacional CLI

Una Pokédex interactiva para tu terminal con los Pokémon #001 al #649 (Generaciones I-V), ¡con sprites, gritos, minijuegos y mucho más! 🎮✨

## ✨ Características

- 📖 Pokédex completa de las Generaciones I a V (Pokémon #001-#649).
- 🖼️ Sprites e iconos renderizados directamente en la terminal.
- 🔊 Gritos de los Pokémon y efectos de sonido (con opción de silenciar).
- 🎨 Varias paletas de color que puedes ir cambiando al vuelo.
- 🧠 Modo quiz para poner a prueba tus conocimientos Pokémon.
- 🌿 Safari Zone para capturar Pokémon.
- 🏆 Gym Challenge: combates contra líderes de gimnasio.
- 🃏 Minijuego de memoria con tarjetas estilo PC desplazables.
- 💾 Caché local de sprites, gritos y datos para jugar sin conexión.
- 🪪 Exportación de tu Trainer Card.
- 🔍 Búsqueda rápida de Pokémon por nombre.

## 🚀 Cómo jugar / ejecutar

Instala las dependencias y lanza la Pokédex:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"

# Inicia la Pokédex interactiva
python3 pokedex_gen1.py
# o, tras instalar con pip:
pokedex
```

Si solo quieres las dependencias de ejecución:

```bash
python3 -m pip install -r pokedex_requirements.txt
```

Comandos útiles:

```bash
python3 pokedex_gen1.py --pokemon pikachu   # Ver un Pokémon concreto
python3 pokedex_gen1.py --quiz              # Modo quiz
python3 pokedex_gen1.py --safari            # Safari Zone
python3 pokedex_gen1.py --gym               # Gym Challenge
python3 pokedex_gen1.py --stats             # Estadísticas
python3 pokedex_gen1.py --trainer-card      # Exportar Trainer Card
python3 pokedex_gen1.py --cache-status      # Estado de la caché
python3 pokedex_gen1.py --prefetch          # Precargar datos para uso offline
```

## 🎮 Controles

- Flechas, `j`/`k` o `w`/`s`: moverse por las listas
- `ENTER`: abrir / seleccionar / continuar
- `/`: buscar
- `g`: menú de quiz
- `h`: Safari Zone
- `B`: Gym Challenge
- `M`: minijuego de memoria
- `p`: cambiar de paleta de color
- `m`: silenciar / activar audio
- `T`: exportar Trainer Card
- `?`: ayuda
- `q` o `ESC`: volver / salir según la pantalla

## 🛠️ Tecnología

- 🐍 **Python 3.9+** como lenguaje principal.
- 🖼️ **Pillow** para el renderizado de sprites e iconos.
- 🔐 **certifi** para las conexiones TLS con PokeAPI / Showdown.
- 🌐 Datos obtenidos de **PokeAPI** y cacheados localmente.
- 🧪 Tests con **pytest** y linting con **ruff**.

## 📦 Parte de mi colección de juegos

Este es uno de mis juegos hobby. ¡Echa un vistazo a mis otros proyectos en mi perfil de GitHub! 🚀
