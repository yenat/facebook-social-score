pipeline {
    agent any
    environment {
        DOCKER_IMAGE = 'facebook-scorer'
    }
    stages {
        stage('Checkout') {
            steps { git url: 'https://github.com/yenat/facebook-social-score.git', branch: 'main' }
        }
        stage('Build') {
            steps { sh 'docker build -t ${DOCKER_IMAGE} .' }
        }
        stage('Deploy') {
            environment {
                FACEBOOK_EMAIL = credentials('facebook-email')
                FACEBOOK_PASSWORD = credentials('facebook-password')
            }
            steps {
                sh '''
                # Create cookies folder if not exists
                mkdir -p ${WORKSPACE}/cookies

                # Stop and remove old container if exists
                docker rm -f ${DOCKER_IMAGE} || true

                docker run -d \
                    --name ${DOCKER_IMAGE} \
                    -p 7070:7070 \
                    -e FACEBOOK_EMAIL=${FACEBOOK_EMAIL} \
                    -e FACEBOOK_PASSWORD=${FACEBOOK_PASSWORD} \
                    -v ${WORKSPACE}/cookies:/app/cookies \
                    --restart unless-stopped \
                    ${DOCKER_IMAGE}
                '''
            }
        }
    }
}
