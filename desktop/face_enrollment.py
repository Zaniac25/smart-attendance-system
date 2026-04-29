"""
Face Enrollment System:
Captures and stores face encodings for each student.

Usage:
    python face_enrollment.py
    python face_enrollment.py --student-id 101
"""

import face_recognition
import cv2
import os
import pickle
import csv
import argparse
import numpy as np
from pathlib import Path


FACES_DIR = "student_faces"
ENCODINGS_FILE = "face_encodings.pkl"


def load_existing_encodings():
    """Load existing face encodings from file."""
    if os.path.exists(ENCODINGS_FILE):
        with open(ENCODINGS_FILE, 'rb') as f:
            return pickle.load(f)
    return {}


def save_encodings(encodings):
    """Save face encodings to file."""
    with open(ENCODINGS_FILE, 'wb') as f:
        pickle.dump(encodings, f)
    print(f"✓ Encodings saved to {ENCODINGS_FILE}")


def capture_face(student_id, student_name):
    """
    Open webcam and capture student's face.
    Press SPACE to capture, Q to quit.
    
    Returns:
        encoding or None
    """
    Path(FACES_DIR).mkdir(exist_ok=True)
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("❌ Camera not found!")
        return None

    print(f"\n📷 Capturing face for: {student_name} (ID: {student_id})")
    print("   - Look directly at the camera")
    print("   - Press SPACE to capture")
    print("   - Press Q to skip\n")

    encoding = None

    while True:
        ret, frame = camera.read()
        if not ret:
            break

        # Show live feed with instructions
        display = frame.copy()
        cv2.putText(display, f"Student: {student_name}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(display, "SPACE = Capture | Q = Skip", (10, 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        # Detect face in real-time for guidance
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb, model="hog")
        for (top, right, bottom, left) in locations:
            cv2.rectangle(display, (left, top), (right, bottom), (0, 255, 0), 2)
            cv2.putText(display, "Face Detected", (left, top - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        cv2.imshow("Face Enrollment", display)
        key = cv2.waitKey(1) & 0xFF

        if key == ord(' '):  
            if not locations:
                print("⚠️  No face detected. Please try again.")
                continue

            # Get encoding
            encodings = face_recognition.face_encodings(rgb, locations)
            if encodings:
                encoding = encodings[0]
                # Save face image
                img_path = os.path.join(FACES_DIR, f"{student_id}.jpg")
                cv2.imwrite(img_path, frame)
                print(f"✓ Face captured and saved: {img_path}")
                break
            else:
                print("⚠️  Could not encode face. Try again.")

        elif key == ord('q'):
            print(f"⏭️  Skipped: {student_name}")
            break

    camera.release()
    cv2.destroyAllWindows()
    return encoding


def enroll_all_students(students_csv="students.csv"):
    """Enroll faces for all students in the CSV."""
    if not os.path.exists(students_csv):
        print(f"❌ {students_csv} not found!")
        return

    encodings = load_existing_encodings()

    students = []
    with open(students_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            students.append(row)

    print(f"\n{'='*50}")
    print(f"Face Enrollment - {len(students)} students")
    print(f"{'='*50}")

    for i, student in enumerate(students, 1):
        sid = student['StudentID'].strip()
        name = student['Name'].strip()

        if sid in encodings:
            print(f"[{i}/{len(students)}] ✓ Already enrolled: {name} — skipping")
            continue

        print(f"\n[{i}/{len(students)}] Enrolling: {name}")
        encoding = capture_face(sid, name)

        if encoding is not None:
            encodings[sid] = {
                'name': name,
                'encoding': encoding
            }
            save_encodings(encodings)
            print(f"✓ Enrolled: {name}")
        else:
            print(f"✗ Failed to enroll: {name}")

    print(f"\n{'='*50}")
    print(f"Enrollment complete! {len(encodings)} students enrolled.")
    print(f"{'='*50}\n")


def enroll_single_student(student_id, students_csv="students.csv"):
    """Enroll a single student by ID."""
    students = {}
    with open(students_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            students[row['StudentID'].strip()] = row['Name'].strip()

    if student_id not in students:
        print(f"❌ Student ID {student_id} not found in {students_csv}")
        return

    encodings = load_existing_encodings()
    name = students[student_id]
    encoding = capture_face(student_id, name)

    if encoding is not None:
        encodings[student_id] = {'name': name, 'encoding': encoding}
        save_encodings(encodings)
        print(f"✓ Enrolled: {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enroll student faces")
    parser.add_argument('--student-id', help="Enroll a single student by ID")
    args = parser.parse_args()

    if args.student_id:
        enroll_single_student(args.student_id)
    else:
        enroll_all_students()