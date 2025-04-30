# ID Verification API

API REST desarrollada con **FastAPI** para validar la identidad de una persona comparando su documento de identidad y su selfie, con opción de verificación cruzada contra la base pública de la **DGII (República Dominicana)**.

## 📦 Características

- Comparación de rostro entre documento y selfie usando **AWS Rekognition**
- Extracción de cédula dominicana desde el documento usando **AWS Textract**
- Consulta de nombre/documento en la **DGII**
- Autenticación mediante **API Key**
- CORS habilitado

## 🚀 Instalación

1. **Clonar el repositorio**

```bash
git clone https://tu-repositorio.git
cd id-verification-api


.env :

AWS_ACCESS_KEY_ID=tu_aws_access_key
AWS_SECRET_ACCESS_KEY=tu_aws_secret_key
AWS_REGION=us-east-1



Respuesta (Ejemplo)
{
  "verification_id": "7b90ffbc-1e6d-4e21-83c2-b706d88f1930",
  "timestamp": "2025-04-30T14:25:12.536342",
  "status": "success",
  "face_verification": {
    "match": true,
    "similarity": 98.5
  },
  "dgii_verification": {
    "performed": true,
    "success": true,
    "nombre": "JUAN PEREZ",
    "documento": "001-1234567-8",
    "cedula_extracted": "00112345678"
  }
}

Tecnologías usadas

FastAPI
AWS Rekognition / Textract (Boto3)
BeautifulSoup
Uvicorn
Python 3.8+


📝 Notas

La verificación en DGII es una scraping no oficial. Puede fallar si el sitio cambia.
La similitud mínima considerada como match facial es 70%.


🔒 Seguridad

Solo las peticiones con API Key válida son aceptadas.
Los archivos subidos se eliminan automáticamente después del procesamiento.