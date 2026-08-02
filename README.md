# Trazabilidad inteligente del banano — PoC

Actividad COIL UPSE. Prueba de Concepto que integra Blockchain (hash
encadenado), Business Intelligence y Desarrollo de Software para la
trazabilidad de la cadena de valor del banano (cosecha -> empaque -> transporte).

## Requisitos
- Docker Desktop
- Python 3.11+

## Puesta en marcha
1. Levantar PostgreSQL:
   `docker run --name pg-trazabilidad -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=trazabilidad -p 5433:5432 -d postgres:16`
2. Crear entorno e instalar dependencias:
   `python -m venv venv ; .\venv\Scripts\Activate.ps1 ; pip install -r requirements.txt`
3. Ejecutar:
   `streamlit run app.py`
4. En la app, pulsar "Cargar datos de ejemplo".

## Componentes
- Captura de eventos (app Streamlit)
- Integridad con SHA-256 encadenado
- Dashboard con indicadores de trazabilidad

## Equipo
- Byron Velecela Mendez
- Skay Alvarado Rodriguez
- Peter Villon Orrala