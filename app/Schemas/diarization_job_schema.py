from app.Schemas.base_schema import BaseSchema

class TranscriptionDiarizationSchema(BaseSchema):
    @staticmethod
    def check_params(data):
        # Liste des paramètres autorisés
        allowed = ["min_speakers", "max_speakers"]
        # Liste des paramètres obligatoires
        required = [] 

        # 1. Validation des paramètres (autorisation + obligation)
        is_valid, errors = BaseSchema.validate(data, required, allowed)

        if not is_valid:
            return False, errors

        # 2. Vérification de logique métier
        try:
            min_spk = data.get('min_speakers')
            max_spk = data.get('max_speakers')
            if min_spk is not None and max_spk is not None:
                # Vérifie la logique et le type
                if int(min_spk) > int(max_spk):
                    return False, ["min_speakers ne peut pas être supérieur à max_speakers"]
            
                # Retourne les données typées en int pour le controller
                return True, {
                    "min_speakers": int(min_spk),
                    "max_speakers": int(max_spk)
                }
            else:
                # Retourne les données telles quelles (None si non fournies)
                return True, {
                    "min_speakers": int(min_spk) if min_spk is not None else None,
                    "max_speakers": int(max_spk) if max_spk is not None else None
                }
        except (ValueError, TypeError):
            return False, ["Les paramètres de locuteurs doivent être des nombres entiers."]
        
def isNumber(value):
    try:
        int(value)
        return True
    except (ValueError, TypeError):
        return False
