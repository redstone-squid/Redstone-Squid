import com.github.jengelman.gradle.plugins.shadow.tasks.ShadowJar
import org.gradle.api.file.DuplicatesStrategy

plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.shadow)
}

dependencies {
    implementation(project(":core"))
    compileOnly(libs.paper.api)

    testImplementation(libs.junit.jupiter)
    testImplementation(kotlin("test"))
}

tasks.named<ShadowJar>("shadowJar") {
    // The deployable plugin bundles the core and Kotlin dependencies.
    archiveClassifier.set("")
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE
    filesMatching("META-INF/*.kotlin_module") {
        duplicatesStrategy = DuplicatesStrategy.INCLUDE
    }
    dependencies {
        // Paper supplies Brigadier; bundling another copy would duplicate server classes.
        exclude(dependency("com.mojang:brigadier:.*"))
    }
}

tasks.jar {
    archiveClassifier.set("plain")
}

tasks.assemble {
    dependsOn(tasks.shadowJar)
}
