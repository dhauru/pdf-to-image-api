from flask import Flask, request, jsonify, send_file
from pdf2image import convert_from_bytes
from PIL import Image
import io
import base64
import requests
import os
from werkzeug.utils import secure_filename
import tempfile
import uuid

app = Flask(__name__)

def pdf_page_to_image(pdf_data, page_number=0, dpi=300):
    """Convert PDF page to image using pdf2image"""
    try:
        # Convert PDF to images
        images = convert_from_bytes(
            pdf_data, 
            dpi=dpi,
            first_page=page_number + 1,  # pdf2image uses 1-indexed pages
            last_page=page_number + 1,
            fmt='PNG'
        )
        
        if not images:
            raise ValueError(f"Could not convert page {page_number + 1}")
            
        # Get the image
        image = images[0]
        
        # Convert to bytes
        img_buffer = io.BytesIO()
        image.save(img_buffer, format='PNG', optimize=True)
        img_data = img_buffer.getvalue()
        
        # Convert to base64
        img_base64 = base64.b64encode(img_data).decode('utf-8')
        
        return img_base64, img_data
        
    except Exception as e:
        if "first page is after last page" in str(e).lower():
            raise ValueError(f"Page {page_number + 1} does not exist in the PDF")
        raise e

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Make.com"""
    return jsonify({
        'status': 'healthy',
        'message': 'PDF to Image API is running (pdf2image)',
        'version': '1.1'
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
        
        # Limit DPI to prevent timeouts on free tier
        dpi = min(dpi, 400)
        
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
            try:
                response = requests.get(pdf_url, timeout=20, stream=True)
                if response.status_code != 200:
                    return jsonify({'error': f'Failed to download PDF: HTTP {response.status_code}'}), 400
                
                # Check content type
                content_type = response.headers.get('content-type', '').lower()
                if 'pdf' not in content_type and not pdf_url.lower().endswith('.pdf'):
                    return jsonify({'error': 'URL does not point to a PDF file'}), 400
                
                pdf_data = response.content
                filename = f"url_pdf_{uuid.uuid4().hex[:8]}"
                
            except requests.exceptions.Timeout:
                return jsonify({'error': 'Timeout downloading PDF from URL'}), 408
            except requests.exceptions.RequestException as e:
                return jsonify({'error': f'Failed to download PDF: {str(e)}'}), 400
            
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
        
        # Check file size (limit for free tier)
        if len(pdf_data) > 10 * 1024 * 1024:  # 10MB limit
            return jsonify({'error': 'PDF file too large. Maximum size: 10MB'}), 400
        
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
    """
    try:
        if not request.json:
            return jsonify({'error': 'JSON payload required'}), 400
            
        pages = request.json.get('pages', [1])
        dpi = min(request.json.get('dpi', 300), 400)  # Limit DPI
        
        # Limit number of pages for free tier
        if len(pages) > 5:
            return jsonify({'error': 'Maximum 5 pages allowed in batch mode'}), 400
        
        # Get PDF data
        pdf_data = None
        if 'pdf_url' in request.json:
            response = requests.get(request.json['pdf_url'], timeout=20)
            if response.status_code != 200:
                return jsonify({'error': 'Failed to download PDF'}), 400
            pdf_data = response.content
        elif 'pdf_base64' in request.json:
            pdf_data = base64.b64decode(request.json['pdf_base64'])
        else:
            return jsonify({'error': 'pdf_url or pdf_base64 required'}), 400
        
        # Check file size
        if len(pdf_data) > 10 * 1024 * 1024:
            return jsonify({'error': 'PDF file too large for batch processing'}), 400
        
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
