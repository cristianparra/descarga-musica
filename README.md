# Descarga Música

Web self-hosted para descargar playlists de YouTube / YouTube Music a **MP3 (320 kbps)** o **FLAC**,
con carátula, metadatos y estilo Apple Music. Pensada para correr en Docker (CasaOS, Portainer, etc.).

## Características
- Login con usuario y contraseña definidos en el compose.
- Formato (MP3 320 / FLAC) y carpeta de destino configurables en *Ajustes* (carpeta del **servidor**).
- Vista previa de carátula y canciones; puedes editar álbum/artista y desmarcar canciones.
- Carátula incrustada en cada archivo + `cover.jpg`; etiquetas ID3 / Vorbis (título, artista, álbum, pista).
- Limpia etiquetas del título como `(Audio)`, `[Official Video]`, `| Lyric Video`.
- Progreso por canción y total. Cada playlist se guarda en su propia carpeta.
- Basado en [yt-dlp](https://github.com/yt-dlp/yt-dlp) y ffmpeg.

> FLAC no mejora la calidad: YouTube entrega audio comprimido (Opus/AAC) y FLAC solo lo conserva sin pérdidas adicionales.

## Instalación (imagen publicada)
1. Descarga [`docker-compose.yml`](docker-compose.yml) y edita `APP_USER`, `APP_PASSWORD` y las rutas de los volúmenes.
2. `docker compose up -d`
3. Abre `http://IP-DEL-SERVIDOR:8095`.

En CasaOS: **+ → Instalar una app personalizada → Importar** y pega el yml.

## Desarrollo / construir local
```
docker compose -f docker-compose.dev.yml up -d --build
```

## Variables de entorno
| Variable | Descripción | Por defecto |
|---|---|---|
| `APP_USER` | Usuario | `admin` |
| `APP_PASSWORD` | Contraseña | `admin` |
| `SECRET_KEY` | Clave para firmar sesiones (opcional) | derivada de usuario/contraseña |
| `MUSIC_DIR` | Raíz de música en el contenedor | `/music` |
| `CONFIG_DIR` | Ajustes en el contenedor | `/config` |

## Cookies (opcional)
Para videos con restricción de edad, coloca un `cookies.txt` (formato Netscape) en la carpeta `/config`.

## Actualizar
YouTube cambia a menudo. Si fallan las descargas, actualiza la imagen (`docker compose pull && docker compose up -d`)
o reconstruye para traer el `yt-dlp` más reciente.

## Aviso legal
Úsalo solo con contenido que tengas derecho a descargar. Respeta los términos de servicio de YouTube y las leyes de copyright de tu país.
