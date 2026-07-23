# Transcribe AI Shared

Ce package contient les composants partagés par les applications backend.
Elle est composée :

* [`AudioManager`](#audiomanager)
* [`RedisQueueService`](#redisqueueservice)
* [`TranscribeAiBaseSettings`](#transcribeaibasesettings)
* [`Database`](#database)

## AudioManager

Classe python travaillant dans un repertoire et effectue
des opérations basiques comme la suppression, la
 sauvegarde et la lecture de fichier.

## RedisQueueService

Classe python travaillant avec une queue redis.
Elle permet de publier et de consommer des messages.

## TranscribeAiBaseSettings

Classe python contenant les paramètres communs à toutes les applications backend.
Elle est utilisée avec Pydantic pour la validation des paramètres.

## Database

Folder contenant les utilitaires suivants :

* Les models
* Les fonctions de manipulation des tables
* Les fonctions de connexion, déconnexion et génération de session
