# Protocolo del servicio local

El comando instalado `bean` es un cliente del servicio local persistente. El
cliente y el servicio se comunican por TCP en `127.0.0.1`; el puerto
predeterminado es `43827` y puede configurarse con `BEANRUNNER_PORT`.

## Delimitación y respuestas

Cada conexión lleva una solicitud JSON terminada por `\n`, seguida de una
respuesta JSON terminada por `\n`. Se permite que TCP fragmente o agrupe
lecturas: el cliente escribe con `sendall` y el servicio lee hasta el
delimitador. Una solicitud no puede exceder 1 MiB.

Las respuestas exitosas incluyen `"ok": true`; los rechazos incluyen
`"ok": false` y un mensaje en `"error"`. El cliente abre una conexión por
operación y una desconexión no detiene trabajos ni el servicio.

## Operaciones

| `operation` | Campos principales | Respuesta |
|---|---|---|
| `ping` | — | Disponibilidad del servicio. |
| `submit` | `command` (argumentos separados), `request_id`, `cwd` | Metadatos e ID del trabajo. |
| `jobs` | `status` opcional | Trabajos y tabla legible. |
| `status` | `job_id` | Metadatos del trabajo. |
| `output` | `job_id` | `stdout` y `stderr` separados. |
| `cancel` | `job_id` | Resultado de la solicitud de cancelación. |
| `events` | `limit` opcional, máximo 500 | Eventos estructurados recientes. |
| `stop` | — | Cierre controlado del servicio local. |

`request_id` es obligatorio para enviar. Si se repite con el mismo comando y
directorio, se devuelve el trabajo ya registrado; reutilizarlo con otro
contenido se rechaza. Los envíos independientes usan claves distintas y crean
trabajos separados.

## Límite de seguridad actual

El servidor rechaza direcciones distintas de `127.0.0.1`. El protocolo no
incluye autenticación ni una lista de redes permitidas, por lo que no debe
exponerse a la LAN, VPN ni Internet. Antes de habilitar acceso remoto se deben
definir y verificar autorización de clientes, redes permitidas y límites de
directorios de trabajo; la semántica de las operaciones locales debe
conservarse.
