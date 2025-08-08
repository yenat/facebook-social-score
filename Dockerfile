pipeline {
    agent any
    environment {
        DOCKER_IMAGE = 'facebook-scorer'
    }
    stages {
        stage('Checkout') {
            steps { 
                git url: 'https://github.com/yenat/facebook-social-score.git', 
                branch: 'main' 
            }
        }
        stage('Build') {
            steps { 
                sh 'docker build -t ${DOCKER_IMAGE} .' 
            }
        }
        stage('Deploy') {
            environment {
                FACEBOOK_EMAIL = credentials('facebook-email')
                FACEBOOK_PASSWORD = credentials('facebook-password')
            }
            steps {
                sh '''
                docker run -d \
                    --name ${DOCKER_IMAGE} \
                    -p 7070:7070 \
                    -e FACEBOOK_EMAIL=${FACEBOOK_EMAIL} \
                    -e FACEBOOK_PASSWORD=${FACEBOOK_PASSWORD} \
                    -v ${WORKSPACE}/cookies:/app \
                    --restart unless-stopped \
                    ${DOCKER_IMAGE}
                '''
            }
        }
    }
    post {
        always {
            sh "docker logs ${DOCKER_IMAGE} --tail 50 || true"
        }
    }
}