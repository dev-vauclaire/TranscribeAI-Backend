from app.Schemas.base_schema import BaseSchema

class TranscriptionBatchSchema(BaseSchema):
    @staticmethod
    def check_params(data):
        # Liste des paramètres autorisés
        allowed = ["language"]
        # Liste des paramètres obligatoires
        required = [] 

        # 1. Validation des paramètres (autorisation + obligation)
        is_valid, errors = BaseSchema.validate(data, required, allowed)

        if not is_valid:
            return False, errors       
            
        # 2. Vérification de logique métier
        try:
            language = data.get('language', 'fr')
            if len(language) != 2:
                return False, ["Le format de la langue doit être de 2 caractères (ex: 'fr')."]
            
            # Retourne les données typées pour le controller
            return True, {
                "language": language
            }
        except ValueError:
            return False, ["Les paramètres de locuteurs doivent être des nombres entiers."]