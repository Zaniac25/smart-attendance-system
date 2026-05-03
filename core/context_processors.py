from .roles import is_admin, is_teacher, is_student
from .models import ChangeRequest  

def user_role(request):
    if not request.user.is_authenticated:
        return {'is_admin': False, 'is_teacher': False, 'is_student': False, 'pending_change_count': 0}
    
    pending = 0
    if request.user.is_superuser:
        pending = ChangeRequest.objects.filter(status='pending').count()
    
    return {
        'is_admin': request.user.is_superuser,
        'is_teacher': request.user.groups.filter(name='Teacher').exists(),
        'is_student': request.user.groups.filter(name='Student').exists(),
        'pending_change_count': pending,
    }