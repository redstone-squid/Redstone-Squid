plugins {
    alias(libs.plugins.fabric.loom)
    alias(libs.plugins.kotlin.jvm)
}

dependencies {
    minecraft(libs.minecraft)
    implementation(libs.fabric.loader)
    implementation(libs.fabric.api)
    implementation(libs.fabric.language.kotlin)

    implementation(project(":platform:fabric-common"))
    include(project(":protocol"))
    include(project(":safe-snapshot"))
    include(project(":core"))
    include(project(":platform:fabric-common"))
}
