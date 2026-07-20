class BaseSchema:
    # Vérifie les paramètres autorisés et obligatoires
    @staticmethod
    def validate(data, required_params, allowed_params):
        errors = []
        for param in required_params:
            if param not in data or data[param] is None or str(data[param]).strip() == "":
                errors.append("Le paramètre " + param + " est manquant")

        received_keys = set(data.keys())
        allowed_keys = set(allowed_params)
        unknown_keys = received_keys - allowed_keys
        
        if unknown_keys:
            return False, [f"Paramètre(s) non autorisé(s) : {', '.join(unknown_keys)}"]
        
        if errors:
            return False, errors
        return True, None