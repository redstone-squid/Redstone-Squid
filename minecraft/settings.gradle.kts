pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
        maven("https://maven.fabricmc.net/") {
            name = "Fabric"
        }
    }
}

dependencyResolutionManagement {
    repositories {
        mavenCentral()
        maven("https://libraries.minecraft.net/") {
            name = "Mojang"
        }
        maven("https://repo.papermc.io/repository/maven-public/") {
            name = "Paper"
        }
        maven("https://maven.fabricmc.net/") {
            name = "Fabric"
        }
    }
}

rootProject.name = "redstone-squid-minecraft"

include(
    "protocol",
    "safe-snapshot",
    "core",
    "platform:paper",
    "platform:fabric-common",
    "platform:fabric-26_1",
)
