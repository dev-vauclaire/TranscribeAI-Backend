from flask import jsonify

def success(data=None, status=200):
    return jsonify({
        "status": "success",
        "data": data
    }), status

def error(message, status=400):
    return jsonify({
        "status": "error",
        "message": message
    }), status
