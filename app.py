from flask import Flask, request, jsonify, send_file
import fitz  # PyMuPDF
import io
import base64
import requests
import os
from werkzeug.utils import secure_filename
import tempfile
import uuid

app = Flask(__name__)

def pdf_page_to_image(pdf_data, page_number=0, dpi=300):
    """Convert PDF page to image and return as base64"""
    # Open PDF from bytes
    doc = fitz.open(stream=pdf_data, filetype="pdf")
    
    # Check if page exists
    if page_number >= len(doc):
        doc.close()
        raise ValueError(f"Page {page_number + 1} does not exist. PDF has {len(doc)} pages.")
    
    page = doc[page_number]
    
    # Convert to image
    mat = fitz.Matrix(dpi/72, dpi/72)
    pix = page.get_pixmap(matrix=mat)
    
    # Convert to PNG bytes
    img_data = pix.tobytes("png")
    doc.close()
    
    # Convert to base64 for JSON response
    img_base64 = base64.b64encode(img_data).decode('utf-8')
    
    return img_base64, img_data

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Make.com"""
    return jsonify({
        'status': 'healthy',
        'message': 'PDF to Image API is running',
        'version': '1.0'
    })

@app.route('/convert', methods=['POST'])
def convert_pdf():
    """
    Main endpoint for Make.com
    
    Accepts:
    1. PDF file upload (multipart/form-data)
    2. PDF from URL (JSON with 'pdf_url')
    3. PDF as base64 (JSON with 'pdf_base64')
    
    Parameters:
    - page: page number (1-indexed, default: 1)
    - dpi: image resolution (default: 300)
    - format: response format ('base64' or 'binary', default: 'base64')
    """
    try:
        # Get parameters
        page_num = int(request.form.get('page', request.json.get('page', 1) if request.json else 1)) - 1
        dpi = int(request.form.get('dpi', request.json.get('dpi', 300) if request.json else 300))
        response_format = request.form.get('format', request.json.get('format', 'base64') if request.json else 'base64')
        
        pdf_data = None
        filename = "converted_page"
        
        # Handle different input methods
        if request.files and 'pdf' in request.files:
            # Method 1: Direct file upload
            file = request.files['pdf']
            if not file.filename.lower().endswith('.pdf'):
                return jsonify({'error': 'File must be a PDF'}), 400
            pdf_data = file.read()
            filename = secure_filename(file.filename.replace('.pdf', ''))
            
        elif request.json and 'pdf_url' in request.json:
            # Method 2: Download from URL
            pdf_url = request.json['pdf_url']
            response = requests.get(pdf_url, timeout=30)
            if response.status_code != 200:
                return jsonify({'error': 'Failed to download PDF from URL'}), 400
            pdf_data = response.content
            filename = f"url_pdf_{uuid.uuid4().hex[:8]}"
            
        elif request.json and 'pdf_base64' in request.json:
            # Method 3: Base64 encoded PDF
            try:
                pdf_data = base64.b64decode(request.json['pdf_base64'])
            except Exception as e:
                return jsonify({'error': 'Invalid base64 PDF data'}), 400
            filename = f"base64_pdf_{uuid.uuid4().hex[:8]}"
            
        else:
            return jsonify({'error': 'No PDF provided. Use file upload, pdf_url, or pdf_base64'}), 400
        
        if not pdf_data:
            return jsonify({'error': 'No PDF data received'}), 400
        
        # Convert PDF to image
        img_base64, img_binary = pdf_page_to_image(pdf_data, page_num, dpi)
        
        # Return response based on format
        if response_format == 'binary':
            return send_file(
                io.BytesIO(img_binary),
                mimetype='image/png',
                as_attachment=True,
                download_name=f'{filename}_page_{page_num + 1}.png'
            )
        else:
            # Default: base64 response (best for Make.com)
            return jsonify({
                'success': True,
                'image_base64': img_base64,
                'filename': f'{filename}_page_{page_num + 1}.png',
                'page': page_num + 1,
                'dpi': dpi,
                'format': 'PNG',
                'size_bytes': len(img_binary)
            })
            
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Conversion failed: {str(e)}'}), 500

@app.route('/convert-batch', methods=['POST'])
def convert_pdf_batch():
    """
    Batch conversion endpoint for multiple pages
    
    JSON payload:
    {
        "pdf_url": "https://example.com/file.pdf",
        "pages": [1, 2, 3],  // page numbers (1-indexed)
        "dpi": 300
    }
    """
    try:
        if not request.json:
            return jsonify({'error': 'JSON payload required'}), 400
            
        pages = request.json.get('pages', [1])
        dpi = request.json.get('dpi', 300)
        
        # Get PDF data
        pdf_data = None
        if 'pdf_url' in request.json:
            response = requests.get(request.json['pdf_url'], timeout=30)
            if response.status_code != 200:
                return jsonify({'error': 'Failed to download PDF'}), 400
            pdf_data = response.content
        elif 'pdf_base64' in request.json:
            pdf_data = base64.b64decode(request.json['pdf_base64'])
        else:
            return jsonify({'error': 'pdf_url or pdf_base64 required'}), 400
        
        # Convert multiple pages
        results = []
        for page_num in pages:
            try:
                img_base64, img_binary = pdf_page_to_image(pdf_data, page_num - 1, dpi)
                results.append({
                    'page': page_num,
                    'success': True,
                    'image_base64': img_base64,
                    'size_bytes': len(img_binary)
                })
            except Exception as e:
                results.append({
                    'page': page_num,
                    'success': False,
                    'error': str(e)
                })
        
        return jsonify({
            'success': True,
            'results': results,
            'total_pages': len(pages)
        })
        
    except Exception as e:
        return jsonify({'error': f'Batch conversion failed: {str(e)}'}), 500

# Make.com webhook test endpoint
@app.route('/test-webhook', methods=['POST'])
def test_webhook():
    """Test endpoint to verify Make.com webhook setup"""
    return jsonify({
        'message': 'Webhook received successfully',
        'data': request.json,
        'headers': dict(request.headers)
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
