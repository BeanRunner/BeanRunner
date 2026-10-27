# BeanRunner

BeanRunner es un prototipo académico para enviar, ejecutar y supervisar
trabajos locales como procesos separados. Se desarrolla para la materia
Programación de Sistemas Avanzados (2026B), CUCEI, Universidad de Guadalajara.

## Hito 1 y avance actual

La versión actual permite iniciar trabajos locales desde una CLI interactiva,
asignarles un ID, ejecutarlos en procesos hijos con concurrencia limitada,
consultar/listar trabajos, cancelarlos y recuperar su código de salida y
`stdout`/`stderr`. Una entrada inválida se informa sin cerrar la sesión.

La CLI interactiva original mantiene su estado en memoria. El comando global
`bean` usa ahora un servicio local persistente que se inicia automáticamente;
el servicio solo escucha en loopback. No se incluye operación remota todavía.

## Requisitos

- Python 3.10 o posterior.
- Sistema objetivo del equipo: Linux (Ubuntu 22.04/24.04 o WSL2).
- La aplicación usa la biblioteca estándar; instalar el comando `bean` requiere
  `pip` y setuptools.
- `make` es opcional.

## Instalación en Ubuntu o WSL2

En Windows, instala Ubuntu si todavía no tienes una distribución:

```powershell
wsl --install -d Ubuntu
```

En la terminal de Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 git make
git clone https://github.com/AdminAlvarez/BeanRunner.git
cd BeanRunner
```

## Ejecución interactiva

Desde la raíz del repositorio:

```bash
python3 -m src.main --interactive
```

Comandos disponibles:

| Comando | Acción |
|---|---|
| `submit <comando>` | Valida, registra y ejecuta un trabajo; muestra su ID. |
| `status <job-id>` | Consulta estado, PID y metadatos del trabajo. |
| `list [ESTADO]` | Lista los trabajos o filtra por estado. |
| `output <job-id>` | Muestra `stdout` y `stderr` cuando estén disponibles. |
| `cancel <job-id>` | Cancela un trabajo en cola o solicita terminar su proceso. |
| `help` | Muestra ayuda. |
| `exit` | Cierra la sesión y cancela procesos todavía activos. |

Ejemplo de sesión:

```text
beanrunner> submit python3 -c "print('hola')"
beanrunner> list
beanrunner> status <job-id>
beanrunner> output <job-id>
beanrunner> exit
```

## Instalar y usar el comando global `bean`

En Windows, instala BeanRunner con el Python que ejecutas desde CMD:

```powershell
py -m pip install .
```

Para desarrollo se puede usar `py -m pip install -e .`. El directorio `Scripts`
de esa instalación de Python debe estar en `PATH`; después abre una terminal
nueva y comprueba `bean --help`.

En Ubuntu/WSL, instala en un entorno virtual para no modificar el Python del
sistema administrado por `apt`:

```bash
sudo apt install -y python3-venv
python3 -m venv ~/.venvs/beanrunner
~/.venvs/beanrunner/bin/python -m pip install .
sudo ln -s "$HOME/.venvs/beanrunner/bin/bean" /usr/local/bin/bean
bean --help
```

Durante el desarrollo, usa `~/.venvs/beanrunner/bin/python -m pip install -e .`
para que la instalación editable apunte al checkout actual.

El comando `bean` inicia el servicio local automáticamente la primera vez y
se conecta a él en invocaciones posteriores. El servicio escucha únicamente en
loopback (`127.0.0.1`); la CLI y el servicio intercambian mensajes JSON
delimitados por línea, listos para extenderse cuando se implemente la red
privada. El protocolo local se describe en
[`docs/technical-guide/local-protocol.md`](docs/technical-guide/local-protocol.md).
No se expone el servicio remoto en esta etapa.

```text
bean sleep 10             # Envia el programa sleep como trabajo
bean run jobs             # Ejecuta un programa cuyo nombre es reservado
bean jobs                 # Lista trabajos persistidos
bean status <job-id>      # Consulta estado y metadatos
bean output <job-id>      # Consulta stdout y stderr
bean cancel <job-id>      # Cancela un trabajo
bean events               # Muestra eventos recientes
bean stop                 # Detiene el servicio local
```

Los argumentos se ejecutan sin shell implícito. Por eso el ejecutable debe
existir en el `PATH` del sistema donde corre el servicio. `bean ls` ejecuta el
programa `ls` en el directorio desde donde se llamó; `bean jobs` lista el
historial de BeanRunner. Usa `bean run <programa>` para ejecutar un nombre que
coincida con un comando administrativo. El estado se guarda fuera del
repositorio:
`%LOCALAPPDATA%\BeanRunner` en Windows, `~/.local/state/beanrunner` en Linux,
o la ruta indicada por `BEANRUNNER_DATA_DIR`.
El puerto local predeterminado es `43827`; puede cambiarse con
`BEANRUNNER_PORT`. La dirección permanece limitada a `127.0.0.1` hasta que se
implemente y proteja el acceso remoto.

Cada invocación genera una clave de solicitud única. La misma clave repetida
por un reintento de transporte devuelve el mismo trabajo; dos invocaciones
independientes del mismo comando crean trabajos distintos.

Los argumentos se separan respetando comillas, pero los trabajos se inician
sin shell implícito. Envía únicamente comandos y programas confiables.
`max_concurrency` es 3 por defecto; el resto de trabajos válidos espera en
cola hasta que haya un espacio disponible.

Sin opciones, `python3 -m src.main` conserva el comportamiento de arranque
simple y termina después de mostrar un mensaje. Para una demostración
automatizada de ejecución y cancelación:

```bash
python3 -m src.main --run-job-demo
```

Para ver los ejemplos de recepción y validación sin ejecutar los comandos:

```bash
python3 -m src.main --demo
```

## Pruebas y verificación

```bash
python3 -m unittest discover -s tests -v
python3 verif/scripts/verify_hito1.py
```

Con `make` también se puede ejecutar:

```bash
make check
make test
```

`make test` ejecuta la suite unitaria y el script end-to-end. Los casos de
prueba están en [`verif/test-cases/`](verif/test-cases/); el reporte de la
verificación actual se guarda en [`verif/results/`](verif/results/).

La suite incluye pruebas para persistencia, salida separada, recuperación de
trabajos interrumpidos, idempotencia concurrente y protocolo local.

## Estructura

```text
src/                         Código del servicio local y la CLI
tests/                       Pruebas unitarias
docs/decisions/              ADR de arquitectura
docs/technical-guide/        Guías técnicas, incluido el modelo de estados
docs/ai-usage/               Registro del apoyo de herramientas de IA
verif/test-cases/            Casos de prueba funcionales
verif/scripts/               Verificación automatizada
verif/results/               Reportes de ejecución
project-management/          Material de gestión del proyecto
```

## Arquitectura y trazabilidad

- [ADR-001](docs/decisions/ADR-001.md): lenguaje y plataforma.
- [ADR-002](docs/decisions/ADR-002.md): aislamiento de procesos y concurrencia.
- [ADR-003](docs/decisions/ADR-003.md): estrategia futura de persistencia; todavía no implementada.
- [Modelo de estados](docs/technical-guide/state-model.md): ciclo de vida
  implementado y funciones futuras distinguidas.
- [Matriz de trazabilidad del Hito 1](verif/traceability-matrix.md): relación
  entre los requisitos, casos de prueba, implementación y evidencia.

## Equipo

| Integrante | Responsabilidad principal | GitHub |
|---|---|---|
| Alvarez Orozco Admin | Liderazgo; motor de procesos y concurrencia | [@AdminAlvarez](https://github.com/AdminAlvarez) |
| Avila Arellano Bryan Aaron | CLI, redes y gestión del proyecto | [@AviAre16](https://github.com/AviAre16) |
| Alvarado Ahedo Alan Ricardo | Persistencia, registros y resiliencia | [@AlanAlvarado23](https://github.com/AlanAlvarado23) |
| Ortega Gutiérrez Franco Josep | QA, trazabilidad y repositorio | [@FrancoJOG](https://github.com/FrancoJOG) |

## Estado de entrega

El prototipo y sus pruebas automatizadas se han verificado en Windows con
Python 3.14.3 y en Ubuntu sobre WSL2 con Python 3.14.4. Consulta los reportes
en [`verif/results/`](verif/results/) para conocer los comandos, resultados y
alcance de cada ejecución.
