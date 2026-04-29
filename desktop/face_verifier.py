"""
Face Verification Module:
Verifies student identity by comparing live face against stored encoding.
Used by the scanner during attendance marking.
"""

try:
    import face_recognition
    import cv2
    import pickle
    import os
    import numpy as np
    FACE_RECOGNITION_AVAILABLE = True
except ImportError as e:
    print(f"⚠️  face_recognition not available: {e}")
    FACE_RECOGNITION_AVAILABLE = False


ENCODINGS_FILE = "face_encodings.pkl"
TOLERANCE = 0.5  

def load_encodings():
    """Load stored face encodings."""
    if not os.path.exists(ENCODINGS_FILE):
        return {}
    with open(ENCODINGS_FILE, 'rb') as f:
        return pickle.load(f)


def verify_face(frame, student_id):
    """
    Verify if the face in the frame matches the enrolled face for the student.
    Returns:
    - success (bool)
    - confidence (float)
    - message (str)
    
    """
    if not FACE_RECOGNITION_AVAILABLE:
        return False, 0.0, "Face recognition not available"

    encodings = load_encodings()

    if student_id not in encodings:
        return False, 0.0, "Face not enrolled"

    stored_encoding = encodings[student_id]['encoding']

    # Convert to RGB for face_recognition
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Detect faces in frame
    face_locations = face_recognition.face_locations(rgb_frame, model="hog")

    if not face_locations:
        return False, 0.0, "No face detected"

    # Get encodings for detected faces
    live_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

    if not live_encodings:
        return False, 0.0, "Could not encode face"

    # Compare each detected face against stored encoding
    for live_encoding in live_encodings:
        distance = face_recognition.face_distance([stored_encoding], live_encoding)[0]
        confidence = round((1 - distance) * 100, 1)

        if distance <= TOLERANCE:
            return True, confidence, f"Verified ({confidence}% match)"

    return False, 0.0, "Face does not match"


def is_enrolled(student_id):
    """Check if a student has an enrolled face."""
    if not FACE_RECOGNITION_AVAILABLE:
        return False
    encodings = load_encodings()
    return student_id in encodings