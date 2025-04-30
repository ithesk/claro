from fastapi import FastAPI, File, UploadFile, Header, HTTPException, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
import boto3
import uuid
import json
import shutil
import requests
import re
import time
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Optional, List
import logging
from dotenv import load_dotenv
import os

load_dotenv()

# Configuración de logging nnn
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("api.log"), logging.StreamHandler()]
)
logger = logging.getLogger("id-verification")

app = FastAPI(title="ID Verification API")

# Configuración CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración
UPLOAD_DIR = "temp_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
VALID_API_KEYS = ["809274pablotestnuevo"]  # Tu clave de prueba

# Configurar AWS
rekognition = boto3.client('rekognition',
                         aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                         aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                         region_name=os.getenv('AWS_REGION', 'us-east-1'))

textract = boto3.client('textract',
                      aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                      aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                      region_name=os.getenv('AWS_REGION', 'us-east-1'))

# Funciones auxiliares
def is_valid_api_key(api_key: str) -> bool:
    return api_key in VALID_API_KEYS

async def save_upload_file(upload_file: UploadFile, destination: str):
    with open(destination, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return destination

def cleanup_files(file_paths: List[str]):
    for file_path in file_paths:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.debug(f"Removed temporary file: {file_path}")

def extract_cedula_from_text(text_list):
    """Extraer número de cédula de los textos encontrados en el documento"""
    # Patrón para una cédula dominicana: XXX-XXXXXXX-X (con o sin guiones)
    pattern = r'\b\d{3}[-]?\d{7}[-]?\d{1}\b'
    
    for text in text_list:
        # Buscar el patrón en el texto
        matches = re.findall(pattern, text)
        if matches:
            # Limpiar guiones si existen
            return re.sub(r'[^0-9]', '', matches[0])
    
    return None

def consultar_dgii_cedula(cedula):
    """
    Consulta información de un ciudadano usando su cédula en el portal de la DGII.
    Adaptado del código Odoo proporcionado.
    """
    try:
        logger.info(f"Consultando cédula en DGII: {cedula}")
        
        # Crear una sesión para mantener cookies
        session = requests.Session()
        
        # URL de la página de consulta
        url = "https://dgii.gov.do/app/WebApps/ConsultasWeb/consultas/ciudadanos.aspx"
        
        # Headers para simular navegador
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3',
        }
        
        # Paso 1: Obtener la página inicial para capturar tokens
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extraer tokens necesarios para el formulario
        viewstate = soup.find('input', {'id': '__VIEWSTATE'})
        eventvalidation = soup.find('input', {'id': '__EVENTVALIDATION'})
        
        if not viewstate or not eventvalidation:
            return {'success': False, 'error': 'No se pudieron encontrar los tokens del formulario'}
        
        # Paso 2: Enviar el formulario con la cédula
        form_data = {
            '__VIEWSTATE': viewstate['value'],
            '__EVENTVALIDATION': eventvalidation['value'],
            'ctl00$cphMain$txtCedula': cedula,
            'ctl00$cphMain$btnBuscarCedula': 'Buscar'
        }
        
        # Esperar un momento antes de enviar
        time.sleep(1)
        
        response = session.post(url, data=form_data, headers=headers, timeout=30)
        response.raise_for_status()
        
        # Paso 3: Analizar la respuesta
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Buscar la tabla de resultados con el ID específico
        tabla_resultados = soup.find('table', {'id': 'ctl00_cphMain_dvResultadoCedula'})
        
        if not tabla_resultados:
            return {'success': False, 'error': 'No se encontraron resultados para esta cédula'}
        
        # Extraer el nombre de la primera fila, segunda columna
        filas = tabla_resultados.find_all('tr')
        datos = {'success': True}
        
        # Mapeo de filas a campos en nuestro formulario (índice de fila: nombre del campo)
        mapeo_campos = {
            0: 'nombre',      # Primera fila (índice 0) contiene el nombre
            3: 'documento',   # Cuarta fila (índice 3) contiene el documento formateado
        }
        
        for idx, fila in enumerate(filas):
            if idx in mapeo_campos:
                celdas = fila.find_all('td')
                if len(celdas) >= 2:
                    campo = mapeo_campos[idx]
                    datos[campo] = celdas[1].text.strip()
        
        # Verificar que se obtuvo al menos el nombre
        if 'nombre' not in datos:
            return {'success': False, 'error': 'No se pudo extraer el nombre de los resultados'}
        
        logger.info(f"Datos obtenidos de DGII: {datos}")
        return datos
        
    except Exception as e:
        logger.error(f"Error consultando DGII: {str(e)}")
        return {'success': False, 'error': f'Error al consultar DGII: {str(e)}'}

def process_with_rekognition(id_path: str, face_path: str):
    """Procesar imágenes usando AWS Rekognition y extraer información para verificación cruzada"""
    try:
        # Leer archivos
        with open(id_path, "rb") as id_file, open(face_path, "rb") as face_file:
            id_image_bytes = id_file.read()
            face_image_bytes = face_file.read()
        
        # Analizar documento con Textract para extraer texto
        textract_response = textract.detect_document_text(
            Document={'Bytes': id_image_bytes}
        )
        
        # Extraer texto relevante
        extracted_text = []
        for item in textract_response.get('Blocks', []):
            if item['BlockType'] == 'LINE':
                extracted_text.append(item['Text'])
        
        # Buscar cédula en el texto extraído
        cedula = extract_cedula_from_text(extracted_text)
        
        # Detectar rostro en documento
        id_face_response = rekognition.detect_faces(
            Image={'Bytes': id_image_bytes},
            Attributes=['ALL']
        )
        
        # Detectar rostro en selfie
        selfie_face_response = rekognition.detect_faces(
            Image={'Bytes': face_image_bytes},
            Attributes=['ALL']
        )
        
        # Verificar que se detectaron caras
        if not id_face_response['FaceDetails'] or not selfie_face_response['FaceDetails']:
            return {
                "success": False,
                "error": "No se detectaron rostros en alguna de las imágenes",
                "extracted_text": extracted_text,
                "cedula": cedula
            }
        
        # Comparar caras
        compare_response = rekognition.compare_faces(
            SourceImage={'Bytes': face_image_bytes},
            TargetImage={'Bytes': id_image_bytes},
            SimilarityThreshold=70
        )
        
        # Procesar resultado
        similarity = 0
        face_match = False
        
        if compare_response['FaceMatches']:
            face_match = True
            similarity = compare_response['FaceMatches'][0]['Similarity']
        
        return {
            "success": True,
            "face_match": face_match,
            "similarity": similarity,
            "extracted_text": extracted_text,
            "cedula": cedula
        }
            
    except Exception as e:
        logger.error(f"Error in AWS processing: {str(e)}")
        return {"success": False, "error": str(e)}

@app.post("/verify")
async def verify_identity(
    background_tasks: BackgroundTasks,
    id_image: UploadFile = File(...),
    face_image: UploadFile = File(...),
    api_key: Optional[str] = Header(None),
    require_liveness: bool = Form(False),
    verify_dgii: bool = Form(True)  # Nuevo parámetro para decidir si verificar con DGII
):
    """
    Endpoint para verificar identidad comparando ID y selfie, y opcionalmente verificando con DGII
    
    - **id_image**: Imagen del documento de identidad
    - **face_image**: Selfie/foto de la persona
    - **api_key**: Clave API para autenticación
    - **require_liveness**: Si se requiere verificación de vida
    - **verify_dgii**: Si se debe verificar la información con DGII
    """
    # Validar API key
    if not api_key or not is_valid_api_key(api_key):
        raise HTTPException(status_code=403, detail="API key inválida o no proporcionada")
    
    verification_id = str(uuid.uuid4())
    logger.info(f"Starting verification {verification_id}")
    
    # Validar tipos de archivo
    for img in [id_image, face_image]:
        if img.content_type not in ["image/jpeg", "image/png", "application/octet-stream"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Solo se aceptan imágenes en formato JPEG o PNG. Recibido: {img.content_type}"
            )
    
    # Generar rutas para archivos temporales
    id_filename = f"{verification_id}_id.jpg"
    face_filename = f"{verification_id}_face.jpg"
    
    id_path = os.path.join(UPLOAD_DIR, id_filename)
    face_path = os.path.join(UPLOAD_DIR, face_filename)
    
    try:
        # Guardar archivos temporalmente
        await save_upload_file(id_image, id_path)
        await save_upload_file(face_image, face_path)
        
        # Procesar con AWS Rekognition
        result = process_with_rekognition(id_path, face_path)
        
        # Configurar limpieza de archivos
        background_tasks.add_task(cleanup_files, [id_path, face_path])
        
        if not result["success"]:
            return {
                "verification_id": verification_id,
                "timestamp": datetime.now().isoformat(),
                "status": "error",
                "error": result.get("error", "Error desconocido en el procesamiento"),
                "extracted_text": result.get("extracted_text", [])
            }
        
        # Determinar resultado de la verificación facial
        face_verification = {
            "match": result["face_match"],
            "similarity": round(result["similarity"], 2)
        }
        
        # Verificación con DGII si se solicitó y se encontró una cédula
        dgii_verification = {"performed": False}
        if verify_dgii and result.get("cedula"):
            cedula = result["cedula"]
            dgii_result = consultar_dgii_cedula(cedula)
            
            if dgii_result["success"]:
                dgii_verification = {
                    "performed": True,
                    "success": True,
                    "nombre": dgii_result.get("nombre"),
                    "documento": dgii_result.get("documento"),
                    "cedula_extracted": cedula
                }
            else:
                dgii_verification = {
                    "performed": True,
                    "success": False,
                    "error": dgii_result.get("error"),
                    "cedula_extracted": cedula
                }
        
        # Determinar resultado final
        verification_passed = result["face_match"] and result["similarity"] > 80
        
        # Si se verificó con DGII, el resultado final depende de ambas verificaciones
        if dgii_verification["performed"] and dgii_verification["success"]:
            verification_passed = verification_passed and dgii_verification["success"]
        
        # Crear respuesta
        response = {
            "verification_id": verification_id,
            "timestamp": datetime.now().isoformat(),
            "status": "success",
            "verification_passed": verification_passed,
            "face_verification": face_verification,
            "dgii_verification": dgii_verification,
            "extracted_info": {
                "text_found": result["extracted_text"][:10]  # Limitar para la respuesta
            }
        }
        
        logger.info(f"Verification {verification_id} completed: Face Match={result['face_match']}, Score={result['similarity']}, DGII={dgii_verification['performed']}")
        return response
        
    except Exception as e:
        # Asegurar limpieza incluso en caso de error
        background_tasks.add_task(cleanup_files, [id_path, face_path])
        logger.error(f"Error in verification process: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error durante la verificación: {str(e)}")

@app.post("/consultar_cedula")
async def consultar_cedula(
    api_key: Optional[str] = Header(None),
    cedula: str = Form(...)
):
    """
    Endpoint para consultar información de cédula en DGII
    
    - **api_key**: Clave API para autenticación
    - **cedula**: Número de cédula a consultar
    """
    # Validar API key
    if not api_key or not is_valid_api_key(api_key):
        raise HTTPException(status_code=403, detail="API key inválida o no proporcionada")
    
    # Limpiar la cédula de caracteres no numéricos
    cedula_limpia = re.sub(r'\D', '', cedula)
    
    if not cedula_limpia or len(cedula_limpia) < 11:
        raise HTTPException(status_code=400, detail="Cédula inválida o incompleta")
    
    # Consultar en DGII
    resultado = consultar_dgii_cedula(cedula_limpia)
    
    if not resultado["success"]:
        raise HTTPException(status_code=404, detail=resultado.get("error", "No se encontraron resultados"))
    
    return resultado

@app.get("/health")
async def health_check():
    """Endpoint para verificar que la API está funcionando"""
    # Probar conexión a AWS
    aws_status = "not_configured"
    try:
        rekognition.list_collections(MaxResults=1)
        aws_status = "connected"
    except Exception as e:
        aws_status = f"error: {str(e)}"
    
    return {
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "cloud_services": {
            "aws": aws_status
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)