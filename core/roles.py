def is_admin(user):
    return user.is_superuser

def is_teacher(user):
    return user.groups.filter(name='Teacher').exists()

def is_student(user):
    return user.groups.filter(name='Student').exists()

def get_role(user):
    if user.is_superuser:
        return 'admin'
    if is_teacher(user):
        return 'teacher'
    if is_student(user):
        return 'student'
    return None


ROLE_HOME = {
    'admin':   'dashboard',      # url name
    'teacher': 'teacher_dashboard',
    'student': 'student_dashboard',
}